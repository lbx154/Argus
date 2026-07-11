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

import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from ..core.event_catalog import EventType

log = logging.getLogger(__name__)

_ROLE_SUBDIRS = ("engineer", "reviewer")
_ZERO = {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 0}


def _argus_source_root() -> Path:
    """The argus package dir (``…/argus_skill``) — a path inside the repo."""
    from ..skills.builtins import builtin_skill_source_path

    return builtin_skill_source_path().resolve().parent


def _collect_source_skill_names() -> set[str]:
    """Casefolded names of every skill ALREADY in the argus source tree —
    builtins (incl. stubs) + every vertical's skills. Used to skip factory
    skills (the runtime library re-seeds those from source), so tidy only
    routes genuinely new, agent-distilled skills."""
    from ..skills.builtins import iter_builtin_skill_texts, iter_vertical_skill_texts
    from ..skills.store import Skill
    from ..skills.vertical_select import VERTICALS

    names: set[str] = set()

    def _add(it: Any) -> None:
        for filename, text in it:
            if filename.endswith(".md"):
                nm = Skill.parse(text).name.strip().casefold()
                if nm:
                    names.add(nm)

    try:
        _add(iter_builtin_skill_texts())
    except Exception:  # noqa: BLE001 — best-effort
        log.warning("tidy: failed to scan builtin source skills", exc_info=True)
    for vertical in VERTICALS:
        try:
            _add(iter_vertical_skill_texts(vertical))
        except Exception:  # noqa: BLE001 — best-effort per vertical
            pass
    return names


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}.{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


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
    _atomic_write(dest, skill.render())
    return dest


def _autocommit_enabled() -> bool:
    """Whether end-of-mission skill tidy-up may git-commit to the argus source repo.

    Default OFF: for an editable install the source root IS the operator's own
    working tree (often on ``main`` with hand-staged work), and an autonomous
    commit there collides with a hand-driven git workflow. Opt in with
    ``ARGUS_SKILL_AUTOCOMMIT_SKILLS=1``.
    """
    return os.environ.get("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def commit_to_source(paths: list[Path], message: str) -> bool:
    """Best-effort ``git add`` + ``git commit`` of ``paths`` in the argus repo.

    Default OFF (``ARGUS_SKILL_AUTOCOMMIT_SKILLS`` unset): the tidied skill files
    are written but NOT committed, so an autonomous mission never commits to the
    operator's live editable-install repo. When enabled, commits ONLY the given
    paths via ``git commit --only`` — never the operator's ambient staged index,
    so a hand-staged change can never be swept into an automated commit. Returns
    ``True`` only on an actual commit. Any failure (read-only package, non-git
    tree, nothing to commit, commit error) → ``False``, logged, never raises. The
    files are left in the working tree so a later run / the operator can commit.
    """
    if not paths:
        return False
    if not _autocommit_enabled():
        log.info(
            "commit_to_source: skill auto-commit disabled (default); %d skill "
            "file(s) written, not committed. Set ARGUS_SKILL_AUTOCOMMIT_SKILLS=1 "
            "to opt in.",
            len(paths),
        )
        return False
    root = _argus_source_root()
    strs = [str(p) for p in paths]
    try:
        subprocess.run(
            ["git", "-C", str(root), "add", "--", *strs],
            check=True, capture_output=True,
        )
        # --only: commit ONLY these paths, never the operator's ambient staged
        # index, so a hand-staged change is never swept into an automated commit.
        subprocess.run(
            ["git", "-C", str(root), "commit", "--only", "-m", message, "--", *strs],
            check=True, capture_output=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — commit is best-effort
        log.warning("commit_to_source failed (%s)", type(exc).__name__)
        return False


def tidy_runtime_skills_to_source(
    runtime_store: Any,
    classify: Callable[..., Any],
    *,
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
    written: list[Path] = []

    for summ in summaries:
        name = (summ.get("name") or "").strip()
        if not name or name.casefold() in source_names:
            continue  # factory skill already in source → skip
        try:
            skill = runtime_store.load(summ.get("path") or "")
            task_hint = " ".join(getattr(skill, "task_history", []) or []) or (
                getattr(skill, "description", "") or ""
            )
            verdict = classify(
                content=getattr(skill, "content", "") or "", task=task_hint
            )
            placement = getattr(verdict, "placement", "stay")
            vertical = getattr(verdict, "vertical", "") or ""
            why = getattr(verdict, "why", "") or ""
            role = summ.get("role") or ""

            if placement == "global":
                dest = write_skill_to_source(skill, "global", role=role)
                if dest is not None:
                    written.append(dest)
                    counts["to_builtin"] += 1
                    _emit(on_event, f"{name} → builtin ({why})")
                else:
                    counts["stayed"] += 1
            elif placement == "vertical":
                dest = write_skill_to_source(
                    skill, "vertical", vertical=vertical, role=role
                )
                if dest is not None:
                    written.append(dest)
                    counts["to_vertical"] += 1
                    _emit(on_event, f"{name} → verticals/{vertical} ({why})")
                else:
                    counts["stayed"] += 1
            else:
                counts["stayed"] += 1
        except Exception:  # noqa: BLE001 — one bad skill never aborts the sweep
            counts["errors"] += 1
            log.warning("tidy: failed on %s", summ.get("path"), exc_info=True)

    if written:
        msg = (
            f"chore(skills): tidy {len(written)} distilled skill(s) "
            f"into argus source [manager]"
        )
        if not commit_to_source(written, msg):
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
            runtime, manager.classify_skill_placement, on_event=on_event
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


def _emit(on_event: Any, text: str) -> None:
    if callable(on_event):
        try:
            on_event({"type": EventType.SKILL_TIDIED, "text": text})
        except Exception:  # noqa: BLE001 — event sink must never break tidy
            pass


__all__ = [
    "tidy_after_mission",
    "tidy_runtime_skills_to_source",
    "write_skill_to_source",
    "commit_to_source",
]
