"""Mission View on-disk state: schema defaults, file locking, and load/bootstrap.

This module owns everything needed to get a ``dict`` mission-view payload off
disk (or produce an empty/bootstrapped one) without knowing anything about how
individual events are reduced into that payload.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..event_catalog import EventType, canonical_event_type

MISSION_VIEW_FILE = "mission-view.json"
MISSION_VIEW_LOCK_FILE = "mission-view.lock"
MISSION_VIEW_SCHEMA_VERSION = 2
MISSION_TIMELINE_LIMIT = 120
MISSION_ROLE_WORK_LIMIT_PER_ROLE = 40
MISSION_BOOTSTRAP_MAX_BYTES = 8 * 1024 * 1024
MISSION_SKILL_CONTENT_MAX_BYTES = 128 * 1024

_ROLE_NAMES = ("manager", "planner", "engineer", "reviewer")
_PIPELINE_ROLE_NAMES = frozenset({"planner", "engineer", "reviewer"})
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()

try:  # pragma: no cover - production daemons are POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


def empty_mission_view() -> dict[str, Any]:
    return {
        "schema_version": MISSION_VIEW_SCHEMA_VERSION,
        "bootstrapped": False,
        "mission": {
            "id": "",
            "title": "",
            "objective": "",
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "elapsed_seconds": 0.0,
            "campaign_started_at": None,
            "campaign_elapsed_seconds": 0.0,
        },
        "stage": {"id": "", "label": ""},
        "round": {"current": 0, "max": 0},
        "active_role": "",
        "roles": [
            {"role": role, "status": "waiting", "label": "Waiting", "updated_at": 0.0}
            for role in _ROLE_NAMES
        ],
        "role_work": [],
        "dag": [],
        "timeline": [],
        "artifacts": [],
        "learned_skills": [],
        "learned_wiki_pages": [],
        "storage": {
            "project_skill_dir": "",
            "global_skill_dir": "",
            "project_skill_count": 0,
            "global_skill_count": 0,
            "skill_history_compressed": 0,
            "wiki_retired_compressed": 0,
            "skill_history_bytes_saved": 0,
            "wiki_retired_bytes_saved": 0,
            "wiki_paths": [],
        },
        "achievement": None,
        "review": {"status": "", "reason": "", "rejected_attempts": 0},
        "outcome": {},
        "last_event_ts": 0.0,
        "updated_at": 0.0,
    }


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / MISSION_VIEW_LOCK_FILE
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
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


def _read_unlocked(root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((root / MISSION_VIEW_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        return empty_mission_view()
    if not isinstance(payload, dict):
        return empty_mission_view()
    schema_version = payload.get("schema_version")
    if schema_version not in {1, MISSION_VIEW_SCHEMA_VERSION}:
        return empty_mission_view()
    if schema_version == 1:
        payload["schema_version"] = MISSION_VIEW_SCHEMA_VERSION
        for key in (
            "hypotheses",
            "experiments",
            "metrics",
            "primary_metric",
            "decision_context",
        ):
            payload.pop(key, None)
    storage_defaults = {
        "project_skill_dir": "",
        "global_skill_dir": "",
        "project_skill_count": 0,
        "global_skill_count": 0,
        "skill_history_compressed": 0,
        "wiki_retired_compressed": 0,
        "skill_history_bytes_saved": 0,
        "wiki_retired_bytes_saved": 0,
        "wiki_paths": [],
    }
    storage = payload.setdefault("storage", {})
    for key, value in storage_defaults.items():
        storage.setdefault(key, value)
    payload.setdefault("learned_wiki_pages", [])
    payload.setdefault("role_work", [])
    payload.setdefault("outcome", {})
    for skill in payload.setdefault("learned_skills", []):
        if isinstance(skill, dict):
            skill.pop("content", None)
            skill.pop("content_truncated", None)
    mission = payload.setdefault("mission", {})
    mission.setdefault("campaign_started_at", None)
    mission.setdefault("campaign_elapsed_seconds", 0.0)
    achievement = payload.get("achievement")
    if (
        isinstance(achievement, dict)
        and str(achievement.get("id") or "").startswith("derived-")
    ):
        payload["achievement"] = None
    return payload


def load_mission_view(root: Path | str) -> dict[str, Any]:
    path = Path(root).expanduser()
    with _locked(path):
        return _read_unlocked(path)


def _write_unlocked(root: Path, view: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = root / MISSION_VIEW_FILE
    fd, tmp_name = tempfile.mkstemp(prefix=".mission-view-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(view, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


_PROJECTED_EVENT_TYPES = frozenset({
    EventType.LIFE_MANAGER_INTENT_COMPLETED,
    EventType.LIFE_MANAGER_STAGE_DECISION,
    EventType.LIFE_PLANNER_START,
    EventType.LIFE_PLANNER_TASK_ADDED,
    EventType.LIFE_PLANNER_VERDICT,
    EventType.LIFE_PLANNER_WAITING,
    EventType.LIFE_PLANNER_TERMINAL_IDLE,
    EventType.LIFE_PLANNER_ERROR,
    EventType.LIFE_MISSION_STARTED,
    EventType.LIFE_MISSION_COMPLETED,
    EventType.LIFE_MISSION_FAILED,
    EventType.ROUND_START,
    EventType.ROUND_MAIN_COMPLETED,
    EventType.ROUND_REVIEW_STARTED,
    EventType.ROUND_REVIEW_DEFERRED,
    EventType.ROUND_REVIEW_COMPLETED,
    EventType.ENGINEER_PROGRESS,
    EventType.RESEARCH_ACHIEVEMENT_CERTIFIED,
    EventType.SKILL_CREATED,
    EventType.SKILL_UPDATED,
    EventType.SKILL_ARCHIVED,
    EventType.SKILL_TIDIED,
    EventType.SKILL_EVOLUTION_COMPLETED,
    EventType.SKILL_HISTORY_COMPRESSED,
    EventType.WIKI_INITIALIZED,
    EventType.WIKI_EVOLUTION_COMPLETED,
    EventType.WIKI_CREATED,
    EventType.WIKI_UPDATED,
    EventType.WIKI_RETIRED,
    EventType.WIKI_PROMOTION_PROMOTED,
    EventType.WIKI_PROMOTION_DEMOTED,
    EventType.WIKI_RETIRED_COMPRESSED,
})


def _tail_jsonl(path: Path, max_bytes: int = MISSION_BOOTSTRAP_MAX_BYTES) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - max_bytes)
            handle.seek(start)
            raw = handle.read()
    except OSError:
        return []
    if start:
        _discard, separator, raw = raw.partition(b"\n")
        if not separator:
            return []
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if canonical_event_type(event.get("type")) not in _PROJECTED_EVENT_TYPES:
            continue
        rows.append(event)
    return rows


def mission_view_handles_event(event_type: Any) -> bool:
    return canonical_event_type(event_type) in _PROJECTED_EVENT_TYPES
