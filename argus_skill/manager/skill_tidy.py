"""End-of-mission skill tidy-up: the Manager promotes distilled skills into the
argus SOURCE tree (and commits them).

When a mission finishes, the runtime skill library (the single global store at
``~/.argus-skill/skills``) holds the playbooks the reviewer distilled while
working — on top of the factory skills seeded from source. This module has the
Manager review the *new* ones (those NOT already in source) and route each into
the argus codebase itself:

* a CROSS-DOMAIN skill → ``argus_skill/builtin_skills/<role>/``
* a domain-specific skill → ``argus_skill/verticals/<v>/skills/<role>/``

so a good lesson becomes a version-controlled, shipped capability. After writing
the files it auto-commits them to the argus repo. Fully fail-soft: a read-only
package, a non-git tree, a commit failure, or a judge error logs and skips —
never blocks mission completion.

Idempotent: a skill written back is seeded into the runtime library on the next
start (now itself a factory skill), so the next tidy finds it already in source
and skips it — no duplication.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from ..core.event_catalog import EventType
from . import source_writeback

log = logging.getLogger(__name__)

_ROLE_SUBDIRS = ("engineer", "reviewer")
_ZERO = {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 0}
_SOURCE_LOCKS: dict[str, threading.Lock] = {}
_SOURCE_LOCKS_GUARD = threading.Lock()

fcntl: Any
try:  # pragma: no cover - production promotion runs on POSIX
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    fcntl = None
else:  # pragma: no cover
    fcntl = _fcntl


@contextmanager
def _source_write_lock() -> Iterator[None]:
    """Serialize source promotion across threads and daemon processes."""
    source_root = source_writeback.source_root()
    key = str(source_root)
    with _SOURCE_LOCKS_GUARD:
        thread_lock = _SOURCE_LOCKS.setdefault(key, threading.Lock())
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    lock_path = Path(tempfile.gettempdir()) / f"argus-skill-tidy-{digest}.lock"
    with thread_lock:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


def _tidy_batch_size() -> int:
    try:
        return max(1, int(os.environ.get("ARGUS_SKILL_TIDY_BATCH_SIZE", "8")))
    except ValueError:
        return 8


def _collect_source_skill_names() -> set[str]:
    """Casefolded names of every skill ALREADY in the argus source tree —
    builtins (incl. stubs) + every vertical's skills. Used to skip factory
    skills (the runtime library re-seeds those from source), so tidy only
    routes genuinely new, agent-distilled skills."""
    from ..skills.builtins import builtin_skill_source_path, vertical_skill_source_path
    from ..skills.store import Skill
    from ..skills.vertical_select import VERTICALS

    names: set[str] = set()

    def _add(root: Path) -> None:
        if not root.is_dir():
            return
        for path in root.rglob("*.md"):
            try:
                name = Skill.parse(path.read_text(encoding="utf-8")).name
            except (OSError, ValueError):
                continue
            normalized = name.strip().casefold()
            if normalized:
                names.add(normalized)

    try:
        _add(builtin_skill_source_path())
    except Exception:  # noqa: BLE001 — best-effort
        log.warning("tidy: failed to scan builtin source skills", exc_info=True)
    for vertical in VERTICALS:
        try:
            _add(vertical_skill_source_path(vertical))
        except Exception:  # noqa: BLE001 — best-effort per vertical
            pass
    return names


def _target_dir(placement: str, vertical: str) -> Path | None:
    """Source directory for a placement: builtin (global) or a vertical's skills.
    Returns ``None`` for an unknown/invalid vertical (so the caller skips)."""
    from ..skills.builtins import builtin_skill_source_path, vertical_skill_source_path
    from ..skills.vertical_select import VERTICALS

    if placement == "vertical":
        if vertical not in VERTICALS or vertical == "research":
            return None
        return vertical_skill_source_path(vertical)
    return builtin_skill_source_path()


def write_skill_to_source(
    skill: Any, placement: str, *, vertical: str = "", role: str = ""
) -> Path | None:
    """Write ``skill``'s rendered markdown into the argus source tree at
    ``<target>/<role>/<slug>.md`` (role ``engineer``/``reviewer``; otherwise the
    target's top level), with slug-collision avoidance. Returns the path written,
    or ``None`` when the target is invalid. Raises on IO error (caller isolates)."""
    from ..skills.store import _slugify

    target = _target_dir(placement, vertical)
    if target is None:
        return None
    role_dir = target / role if role in _ROLE_SUBDIRS else target
    base = _slugify(getattr(skill, "name", "") or "") or "skill"
    dest = role_dir / f"{base}.md"
    idx = 2
    while dest.exists():
        dest = role_dir / f"{base}-{idx}.md"
        idx += 1
    source_writeback.atomic_write(dest, skill.render())
    return dest


def tidy_runtime_skills_to_source(
    runtime_store: Any,
    classify: Callable[..., Any],
    *,
    classify_batch: Callable[[list[dict[str, str]]], Any] | None = None,
    on_event: Any = None,
) -> dict[str, int]:
    """Route the runtime library's new non-factory skills into
    the argus source tree, then commit the batch.

    ``classify(content=, task=)`` returns a ``PlacementVerdict``
    (``global`` → builtin, ``vertical`` → that vertical's skills, ``stay`` →
    leave). Best-effort per skill; one commit for all files written. Returns
    counts ``{"to_builtin", "to_vertical", "stayed", "errors"}``.
    """
    counts = dict(_ZERO)
    try:
        summaries = runtime_store.list_summaries()
    except Exception:  # noqa: BLE001
        log.warning("tidy: failed to list runtime skills", exc_info=True)
        return counts

    source_names = _collect_source_skill_names()
    pending: list[dict[str, Any]] = []
    for summ in summaries:
        name = (summ.get("name") or "").strip()
        if not name or name.casefold() in source_names:
            continue  # factory skill already in source → skip
        try:
            skill = runtime_store.load(summ.get("path") or "")
            task_hint = " ".join(getattr(skill, "task_history", []) or []) or (
                getattr(skill, "description", "") or ""
            )
            pending.append({
                "name": name,
                "skill": skill,
                "task": task_hint,
                "content": getattr(skill, "content", "") or "",
                "role": summ.get("role") or "",
            })
        except Exception:  # noqa: BLE001 - one unreadable skill stays isolated
            counts["errors"] += 1
            log.warning("tidy: failed on %s", summ.get("path"), exc_info=True)

    verdicts: dict[str, Any] = {}
    if classify_batch is not None and pending:
        size = _tidy_batch_size()
        for start in range(0, len(pending), size):
            batch = pending[start : start + size]
            try:
                result = classify_batch([
                    {
                        "name": row["name"],
                        "task": row["task"],
                        "content": row["content"],
                    }
                    for row in batch
                ])
                if isinstance(result, dict):
                    verdicts.update(result)
            except Exception:  # noqa: BLE001 - conservative stay on batch failure
                log.warning("tidy: batch placement failed", exc_info=True)
    elif pending:
        for row in pending:
            try:
                verdicts[row["name"]] = classify(
                    content=row["content"], task=row["task"]
                )
            except Exception:  # noqa: BLE001
                log.warning("tidy: placement failed for %s", row["name"], exc_info=True)

    written: list[Path] = []
    with _source_write_lock():
        # A second scan under the cross-process lock closes the race between two
        # daemons that classified the same new runtime skill concurrently.
        source_names = _collect_source_skill_names()
        for row in pending:
            name = row["name"]
            if name.casefold() in source_names:
                continue
            try:
                skill = row["skill"]
                verdict = verdicts.get(name)
                placement = getattr(verdict, "placement", "stay")
                vertical = getattr(verdict, "vertical", "") or ""
                why = getattr(verdict, "why", "") or ""
                role = row["role"]

                if placement == "global":
                    dest = write_skill_to_source(skill, "global", role=role)
                    if dest is not None:
                        written.append(dest)
                        source_names.add(name.casefold())
                        counts["to_builtin"] += 1
                        _emit(
                            on_event,
                            text=f"{name} → builtin ({why})",
                            name=name,
                            placement="global",
                            path=dest,
                        )
                    else:
                        counts["stayed"] += 1
                elif placement == "vertical":
                    dest = write_skill_to_source(
                        skill, "vertical", vertical=vertical, role=role
                    )
                    if dest is not None:
                        written.append(dest)
                        source_names.add(name.casefold())
                        counts["to_vertical"] += 1
                        _emit(
                            on_event,
                            text=f"{name} → verticals/{vertical} ({why})",
                            name=name,
                            placement="vertical",
                            vertical=vertical,
                            path=dest,
                        )
                    else:
                        counts["stayed"] += 1
                else:
                    counts["stayed"] += 1
            except Exception:  # noqa: BLE001 - one bad skill never aborts the sweep
                counts["errors"] += 1
                log.warning("tidy: failed on %s", name, exc_info=True)

        if written:
            msg = (
                f"chore(skills): tidy {len(written)} distilled skill(s) "
                f"into argus source [manager]"
            )
            if not source_writeback.commit_to_source(written, msg):
                log.info(
                    "tidy: wrote %d skill file(s) but could not commit "
                    "(left in working tree)",
                    len(written),
                )
    return counts


def tidy_after_mission(
    project_root: Path | str,
    runner: Any,
    *,
    project_state_dir: Path | str | None = None,
    on_event: Any = None,
) -> dict[str, int]:
    """Route this project's active runtime skills into source when opted in.

    ``project_state_dir`` selects the real project-layer store; the global path
    fallback exists only for legacy direct callers. Never raises.
    """
    try:
        from ..core.paths import skills_global_root
        from ..skills.store import SkillStore
        from ._core import Manager

        runtime_dir = (
            Path(project_state_dir) / "skills"
            if project_state_dir is not None
            else skills_global_root()
        )
        runtime = SkillStore(runtime_dir)
        manager = Manager(Path(project_root), runner)
        counts = tidy_runtime_skills_to_source(
            runtime,
            manager.classify_skill_placement,
            classify_batch=manager.classify_skill_placements,
            on_event=on_event,
        )
        # Data-domain promotion sweep. Headless: this NEVER writes to source — it
        # only SURFACES proven, unpromoted data domains for the operator to
        # approve (promotion to the argus source is an irreversible outward
        # change that requires explicit user approval; the actual writeback runs
        # via domain_tidy.promote_data_domain(approved=True) from an interactive
        # surface). Gated by ARGUS_SKILL_PROMOTE_DOMAINS; fail-soft.
        try:
            from .domain_tidy import tidy_domains_after_mission

            tidy_domains_after_mission(Path(project_root), approve=None, on_event=on_event)
        except Exception:  # noqa: BLE001 — promotion sweep must never block tidy
            log.debug("domain promotion sweep failed", exc_info=True)
        return counts
    except Exception:  # noqa: BLE001 — tidy-up must never block mission completion
        log.warning("tidy_after_mission: setup failed; skipping tidy", exc_info=True)
        return dict(_ZERO)


def _emit(
    on_event: Any,
    *,
    text: str,
    name: str,
    placement: str,
    path: Path,
    vertical: str = "",
) -> None:
    if callable(on_event):
        try:
            on_event({
                "type": EventType.SKILL_TIDIED,
                "name": name,
                "placement": placement,
                "vertical": vertical,
                "path": str(path),
                "text": text,
            })
        except Exception:  # noqa: BLE001 — event sink must never break tidy
            pass


__all__ = [
    "tidy_after_mission",
    "tidy_runtime_skills_to_source",
    "write_skill_to_source",
]
