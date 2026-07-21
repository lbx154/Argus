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
import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from ..core.event_catalog import EventType
from . import source_writeback

log = logging.getLogger(__name__)

_ROLE_SUBDIRS = ("engineer", "reviewer", "planner", "manager")
_ZERO = {"to_builtin": 0, "to_vertical": 0, "stayed": 0, "errors": 0}
_ZERO_SHARED = {
    "to_shared": 0,
    "to_vertical_shared": 0,
    "updated": 0,
    "stayed": 0,
    "cached": 0,
    "errors": 0,
}
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
def _path_write_lock(root: Path, label: str) -> Iterator[None]:
    """Serialize Skill writes for one shared root across processes."""
    key = str(Path(root).resolve())
    with _SOURCE_LOCKS_GUARD:
        thread_lock = _SOURCE_LOCKS.setdefault(key, threading.Lock())
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    lock_path = (
        Path(tempfile.gettempdir()) / f"argus-skill-{label}-{digest}.lock"
    )
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


def _source_write_lock() -> Iterator[None]:
    return _path_write_lock(source_writeback.source_root(), "source")


def _shared_write_lock(shared_root: Path) -> Iterator[None]:
    return _path_write_lock(shared_root, "shared")


def _tidy_batch_size() -> int:
    try:
        return max(1, int(os.environ.get("ARGUS_SKILL_TIDY_BATCH_SIZE", "8")))
    except ValueError:
        return 8


def _collect_source_skill_names() -> set[tuple[str, str]]:
    """Role-qualified names of every skill ALREADY in the argus source tree —
    builtins (incl. stubs) + every vertical's skills. Used to skip factory
    skills (the runtime library re-seeds those from source), so tidy only
    routes genuinely new, agent-distilled skills."""
    from ..skills.builtins import builtin_skill_source_path, vertical_skill_source_path
    from ..skills.store import Skill
    from ..skills.vertical_select import VERTICALS

    names: set[tuple[str, str]] = set()

    def _add(root: Path) -> None:
        if not root.is_dir():
            return
        for path in root.rglob("*.md"):
            try:
                skill = Skill.parse(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            normalized = skill.name.strip().casefold()
            if normalized:
                rel = path.relative_to(root).parts
                role = (
                    rel[0]
                    if len(rel) > 1 and rel[0] in _ROLE_SUBDIRS
                    else "general"
                )
                names.add((role, normalized))

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
        if vertical not in VERTICALS:
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
        role = str(summ.get("role") or "general")
        if not name or (role, name.casefold()) in source_names:
            continue  # factory skill already in source → skip
        try:
            skill = runtime_store.load(summ.get("path") or "")
            task_hint = " ".join(getattr(skill, "task_history", []) or []) or (
                getattr(skill, "description", "") or ""
            )
            pending.append({
                "candidate_id": _ledger_key(skill, role),
                "name": name,
                "skill": skill,
                "task": task_hint,
                "content": getattr(skill, "content", "") or "",
                "role": role,
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
                        "candidate_id": row["candidate_id"],
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
                verdicts[row["candidate_id"]] = classify(
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
            role = row["role"]
            if (role, name.casefold()) in source_names:
                continue
            try:
                skill = row["skill"]
                verdict = verdicts.get(row["candidate_id"]) or verdicts.get(name)
                placement = getattr(verdict, "placement", "stay")
                vertical = getattr(verdict, "vertical", "") or ""
                why = getattr(verdict, "why", "") or ""
                if placement == "global":
                    dest = write_skill_to_source(skill, "global", role=role)
                    if dest is not None:
                        written.append(dest)
                        source_names.add((role, name.casefold()))
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
                        source_names.add((role, name.casefold()))
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


def _propagation_digest(skill: Any) -> str:
    payload = {
        "skill_id": str(getattr(skill, "skill_id", "") or ""),
        "name": str(getattr(skill, "name", "") or ""),
        "description": str(getattr(skill, "description", "") or ""),
        "category": str(getattr(skill, "category", "") or ""),
        "content": str(getattr(skill, "content", "") or ""),
        "version": int(getattr(skill, "version", 1) or 1),
        "protected": bool(getattr(skill, "protected", False)),
        "successful_reuses": int(getattr(skill, "successful_reuses", 0) or 0),
        "failed_reuses": int(getattr(skill, "failed_reuses", 0) or 0),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _propagation_target_digest(skill: Any) -> str:
    from ..skills.store import shared_skill_digest

    return shared_skill_digest(skill)


def _load_propagation_ledger(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"schema": 1, "entries": {}}
    entries = value.get("entries") if isinstance(value, dict) else None
    return {
        "schema": 1,
        "entries": entries if isinstance(entries, dict) else {},
    }


def _save_propagation_ledger(path: Path, ledger: dict[str, Any]) -> None:
    source_writeback.atomic_write(
        path,
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _ledger_key(skill: Any, role: str) -> str:
    stable = str(getattr(skill, "skill_id", "") or "").strip()
    if stable:
        return stable
    name = str(getattr(skill, "name", "") or "").strip().casefold()
    return f"{role}:{name}"


def _find_shared_skill(store: Any, name: str, role: str) -> Any | None:
    for summary in store.list_summaries():
        if (
            str(summary.get("name") or "").casefold() == name.casefold()
            and str(summary.get("role") or "general") == role
        ):
            return store.load(str(summary.get("path") or ""))
    return None


def _shared_skill_path(store: Any, skill: Any, role: str) -> Path:
    from ..skills.store import _slugify

    parent = store.skills_dir / role if role in _ROLE_SUBDIRS else store.skills_dir
    return parent / f"{_slugify(str(skill.name)) or 'skill'}.md"


def _copy_skill_to_shared(store: Any, skill: Any, role: str) -> tuple[str, Path | None]:
    """Create/update one shared Skill without copying task-specific history."""
    from ..skills.store import Skill

    existing = _find_shared_skill(store, str(skill.name), role)
    if existing is not None:
        if bool(getattr(existing, "protected", False)):
            return "conflict", Path(existing.path)
        if str(existing.skill_id or "") != str(skill.skill_id or ""):
            return "conflict", Path(existing.path)
        existing_payload = {
            "name": existing.name,
            "description": existing.description,
            "category": existing.category,
            "content": existing.content,
            "protected": existing.protected,
        }
        candidate_payload = {
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "content": skill.content,
            "protected": skill.protected,
        }
        incoming_version = int(getattr(skill, "version", 1) or 1)
        existing_version = int(existing.version or 1)
        if incoming_version < existing_version:
            return "conflict", Path(existing.path)
        if (
            existing_payload == candidate_payload
            and incoming_version == existing_version
        ):
            return "unchanged", Path(existing.path)
        if incoming_version == existing_version:
            return "conflict", Path(existing.path)
        snapshot = store._version_snapshot_path(existing)
        if snapshot is None:
            return "error", Path(existing.path)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot.exists():
            snapshot.write_text(existing.render(), encoding="utf-8")
        clone = Skill.parse(skill.render())
        clone.task_history = []
        clone.reuse_fingerprints = []
        clone.shared_base_digest = ""
        clone.shared_base_version = 0
        clone.path = existing.path
        store.save(clone)
        return "updated", Path(clone.path)

    clone = Skill.parse(skill.render())
    clone.task_history = []
    clone.reuse_fingerprints = []
    clone.shared_base_digest = ""
    clone.shared_base_version = 0
    clone.path = str(_shared_skill_path(store, clone, role))
    if Path(clone.path).exists():
        return "conflict", Path(clone.path)
    store.save(clone)
    return "created", Path(clone.path)


def _transient_stay(reason: str) -> bool:
    normalized = reason.casefold()
    return any(
        marker in normalized
        for marker in ("unavailable", "error", "no json", "no manager runner")
    )


def _shared_ledger_target_is_current(
    entry: dict[str, Any],
    *,
    expected_digest: str,
) -> bool:
    if str(entry.get("digest") or "") != expected_digest:
        return False
    placement = str(entry.get("placement") or "")
    if placement == "stay":
        return True
    path = Path(str(entry.get("path") or ""))
    if not path.is_file():
        return False
    try:
        from ..skills.store import Skill

        shared = Skill.parse(path.read_text(encoding="utf-8"), str(path))
    except (OSError, ValueError):
        return False
    return _propagation_target_digest(shared) == str(
        entry.get("target_digest") or ""
    )


def _shared_skill_records(shared_root: Path, skill_id: str) -> list[tuple[Any, bool]]:
    """Return active and archived shared copies for one stable Skill identity."""
    if not skill_id:
        return []
    from ..skills.store import Skill

    records: list[tuple[Any, bool]] = []
    for path in shared_root.rglob("*.md"):
        try:
            rel = path.relative_to(shared_root)
        except ValueError:
            continue
        if "_history" in rel.parts:
            continue
        try:
            skill = Skill.parse(path.read_text(encoding="utf-8"), str(path))
        except (OSError, ValueError):
            continue
        if str(skill.skill_id or "") != skill_id:
            continue
        records.append((skill, "_archive" in rel.parts))
    return records


def _archive_other_shared_copies(
    records: list[tuple[Any, bool]],
    *,
    replacement: Path | None,
    archive_root: Path,
) -> None:
    from ..skills.lifecycle import archive_skill

    for skill, archived in records:
        path = Path(str(skill.path or ""))
        if (
            archived
            or not path.is_file()
            or (
                replacement is not None
                and path.resolve() == replacement.resolve()
            )
        ):
            continue
        archive_skill(path, archive_root=archive_root)


def propagate_runtime_skills_to_shared(
    runtime_store: Any,
    *,
    shared_root: Path,
    ledger_path: Path,
    classify_batch: Callable[[list[dict[str, str]]], Any],
    on_event: Any = None,
) -> dict[str, int]:
    """Promote reviewed project Skills into immediately visible shared layers."""
    from ..skills.layered import shared_vertical_skills_dir
    from ..skills.store import SkillStore

    counts = dict(_ZERO_SHARED)
    ledger = _load_propagation_ledger(ledger_path)
    entries = ledger["entries"]
    pending: list[dict[str, Any]] = []
    try:
        summaries = runtime_store.list_summaries()
    except Exception:  # noqa: BLE001
        return {**counts, "errors": 1}

    for summary in summaries:
        try:
            skill = runtime_store.load(str(summary.get("path") or ""))
        except Exception:  # noqa: BLE001
            counts["errors"] += 1
            continue
        role = str(summary.get("role") or "general")
        if not str(getattr(skill, "skill_id", "") or "").strip():
            try:
                runtime_store.save(skill)
            except Exception:  # noqa: BLE001
                counts["errors"] += 1
                continue
        digest = _propagation_digest(skill)
        key = _ledger_key(skill, role)
        if _shared_ledger_target_is_current(
            entries.get(key) or {},
            expected_digest=digest,
        ):
            counts["cached"] += 1
            continue
        if bool(getattr(skill, "protected", False)):
            counts["stayed"] += 1
            continue
        pending.append({
            "candidate_id": key,
            "key": key,
            "digest": digest,
            "role": role,
            "skill": skill,
            "name": skill.name,
            "task": " ".join(skill.task_history) or skill.description,
            "content": skill.content,
        })

    if not pending:
        return counts
    try:
        verdicts = classify_batch([
            {
                "name": row["name"],
                "candidate_id": row["candidate_id"],
                "task": row["task"],
                "content": row["content"],
            }
            for row in pending
        ])
    except Exception:  # noqa: BLE001
        return {**counts, "errors": counts["errors"] + 1}
    verdicts = verdicts if isinstance(verdicts, dict) else {}

    with _shared_write_lock(shared_root):
        ledger = _load_propagation_ledger(ledger_path)
        entries = ledger["entries"]
        for row in pending:
            skill = row["skill"]
            shared_records = _shared_skill_records(
                shared_root,
                str(getattr(skill, "skill_id", "") or ""),
            )
            incoming_version = int(getattr(skill, "version", 1) or 1)
            if shared_records:
                base_digest = str(
                    getattr(skill, "shared_base_digest", "") or ""
                )
                active_record_digests = {
                    _propagation_target_digest(existing)
                    for existing, archived in shared_records
                    if not archived
                }
                incoming_target_digest = _propagation_target_digest(skill)
                if (
                    incoming_target_digest not in active_record_digests
                    and (
                        not base_digest
                        or base_digest not in active_record_digests
                    )
                ):
                    counts["stayed"] += 1
                    entries[row["key"]] = {
                        "digest": row["digest"],
                        "target_digest": "",
                        "name": skill.name,
                        "placement": "stay",
                        "vertical": "",
                        "reason": "shared ancestry changed; explicit reconciliation required",
                        "path": "",
                        "updated_at": time.time(),
                    }
                    continue
                newest_version = max(
                    int(getattr(existing, "version", 1) or 1)
                    for existing, _archived in shared_records
                )
                newest = [
                    (existing, archived)
                    for existing, archived in shared_records
                    if int(getattr(existing, "version", 1) or 1)
                    == newest_version
                ]
                if (
                    newest_version > incoming_version
                    or all(archived for _existing, archived in newest)
                ):
                    counts["stayed"] += 1
                    entries[row["key"]] = {
                        "digest": row["digest"],
                        "target_digest": "",
                        "name": skill.name,
                        "placement": "stay",
                        "vertical": "",
                        "reason": "newer or retired shared version already exists",
                        "path": "",
                        "updated_at": time.time(),
                    }
                    continue
                active_newest = [
                    existing
                    for existing, archived in newest
                    if not archived
                ]
                if (
                    newest_version == incoming_version
                    and active_newest
                    and all(
                        _propagation_target_digest(existing)
                        != _propagation_target_digest(skill)
                        for existing in active_newest
                    )
                ):
                    counts["stayed"] += 1
                    entries[row["key"]] = {
                        "digest": row["digest"],
                        "target_digest": "",
                        "name": skill.name,
                        "placement": "stay",
                        "vertical": "",
                        "reason": "conflicting shared content at the same version",
                        "path": "",
                        "updated_at": time.time(),
                    }
                    continue
            verdict = (
                verdicts.get(row["candidate_id"])
                or verdicts.get(row["name"])
            )
            placement = str(getattr(verdict, "placement", "stay") or "stay")
            vertical = str(getattr(verdict, "vertical", "") or "")
            reason = str(getattr(verdict, "why", "") or "")
            retire_existing = False
            if placement == "stay":
                if _transient_stay(reason):
                    counts["errors"] += 1
                    continue
                counts["stayed"] += 1
                path = None
                retire_existing = True
            else:
                target_root = (
                    shared_root
                    if placement == "global"
                    else shared_vertical_skills_dir(shared_root, vertical)
                )
                if target_root is None:
                    counts["errors"] += 1
                    continue
                result, path = _copy_skill_to_shared(
                    SkillStore(target_root),
                    skill,
                    row["role"],
                )
                if result == "created":
                    key = (
                        "to_shared"
                        if placement == "global"
                        else "to_vertical_shared"
                    )
                    counts[key] += 1
                    retire_existing = True
                elif result == "updated":
                    counts["updated"] += 1
                    retire_existing = True
                elif result == "unchanged":
                    counts["cached"] += 1
                    retire_existing = True
                elif result == "conflict":
                    counts["stayed"] += 1
                    placement = "stay"
                    reason = "shared name conflict"
                else:
                    counts["errors"] += 1
                    continue
                if path is not None:
                    _emit(
                        on_event,
                        text=f"{skill.name} → {placement} shared Skill",
                        name=skill.name,
                        placement=placement,
                        vertical=vertical,
                        path=path,
                    )
            if retire_existing:
                _archive_other_shared_copies(
                    shared_records,
                    replacement=path,
                    archive_root=shared_root / "_archive",
                )
            target_digest = ""
            if path is not None and path.is_file():
                try:
                    from ..skills.store import Skill

                    target_digest = _propagation_target_digest(
                        Skill.parse(path.read_text(encoding="utf-8"), str(path))
                    )
                except (OSError, ValueError):
                    target_digest = ""
            if retire_existing and target_digest:
                skill.shared_base_digest = target_digest
                try:
                    from ..skills.store import Skill

                    shared_skill = Skill.parse(
                        path.read_text(encoding="utf-8"),
                        str(path),
                    )
                    skill.shared_base_version = int(shared_skill.version or 1)
                    runtime_store.save(skill)
                except (OSError, ValueError):
                    counts["errors"] += 1
            entries[row["key"]] = {
                "digest": row["digest"],
                "target_digest": target_digest,
                "name": skill.name,
                "placement": placement,
                "vertical": vertical,
                "reason": reason,
                "path": str(path or ""),
                "updated_at": time.time(),
            }
        _save_propagation_ledger(ledger_path, ledger)
    return counts


def propagate_after_mission(
    project_root: Path | str,
    runner: Any,
    *,
    project_state_dir: Path | str | None,
    shared_root: Path | str,
    on_event: Any = None,
) -> dict[str, int]:
    """Manager-route this project's changed Skills into shared runtime layers."""
    if project_state_dir is None or runner is None:
        return dict(_ZERO_SHARED)
    try:
        from ..skills.store import SkillStore
        from ._core import Manager

        state = Path(project_state_dir)
        manager = Manager(
            Path(project_root),
            runner,
            manager_session_root=state,
        )
        return propagate_runtime_skills_to_shared(
            SkillStore(state / "skills"),
            shared_root=Path(shared_root),
            ledger_path=state / "skill-propagation.json",
            classify_batch=manager.classify_skill_placements,
            on_event=on_event,
        )
    except Exception:  # noqa: BLE001
        log.warning("propagate_after_mission: setup failed", exc_info=True)
        return {**_ZERO_SHARED, "errors": 1}


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
    "propagate_after_mission",
    "propagate_runtime_skills_to_shared",
    "tidy_after_mission",
    "tidy_runtime_skills_to_source",
    "write_skill_to_source",
]
