"""Launch settings for a project's first task, and the optional hold before it starts.

A new user's first request often becomes a long autonomous run. Two small
mechanisms make that start deliberate without another model call:

* Launch settings (time to a first checkable result, research ambition, output
  form) chosen in the composer travel with the message as plain operator text,
  so the Manager reads them through the same channel as the request itself.
* A hold: the request is classified, planned and queued as usual, but the
  executor is not started. The project shows what Argus understood and what it
  queued; the operator starts it, or discards it and rewrites the request.

The hold is one small file in the project state directory. It is advisory: any
later daemon start (an explicit Run, another task, an answered question)
supersedes it, and a stale file is ignored once the daemon runs or the held
work is gone.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..core.launch_settings import (
    launch_note,
    launch_target_level_stated,
    normalize_launch_settings,
)

LAUNCH_HOLD_FILENAME = "launch_hold.json"
_ACTIVE_STATUSES = {"pending", "running", "in_progress", "claimed", "parked"}

__all__ = [
    "LAUNCH_HOLD_FILENAME", "clear_launch_hold", "discard_held_work", "launch_hold_view",
    "launch_note", "launch_target_level_stated", "normalize_launch_settings",
    "read_launch_hold", "write_launch_hold",
]


def hold_path(life_dir: Path | str) -> Path:
    return Path(life_dir) / LAUNCH_HOLD_FILENAME


def write_launch_hold(
    life_dir: Path | str,
    result: dict[str, Any],
    settings: dict[str, Any] | None,
    *,
    operator_text: str = "",
) -> dict[str, Any]:
    item = result.get("item") if isinstance(result.get("item"), dict) else {}
    record = {
        "created_at": time.time(),
        "item_id": str(item.get("id") or ""),
        "title": str(item.get("title") or item.get("objective") or operator_text or "")[:200],
        "request": str(operator_text or "")[:4000],
        "continuous": bool(result.get("continuous")),
        "settings": settings or {},
    }
    path = hold_path(life_dir)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return record


def read_launch_hold(life_dir: Path | str) -> dict[str, Any] | None:
    try:
        payload = json.loads(hold_path(life_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def clear_launch_hold(life_dir: Path | str) -> bool:
    try:
        hold_path(life_dir).unlink()
        return True
    except FileNotFoundError:
        return False


def _understood(life_dir: Path) -> dict[str, Any]:
    """What the Manager recorded for this request: goal, requirements, assumptions."""
    try:
        from ..core.project_contract import load_contract

        contract = load_contract(life_dir)
    except Exception:  # noqa: BLE001 - the card still shows the queued plan
        contract = None
    if contract is None:
        return {}
    return {
        "objective": contract.objective[:2000],
        "requirements": [
            {"text": clause.text[:400], "authority": clause.authority}
            for clause in contract.clauses[:12]
        ],
        "exclusions": [text[:400] for text in contract.exclusions[:8]],
        "assumptions": [text[:400] for text in contract.assumptions[:8]],
    }


def _field(item: Any, name: str) -> Any:
    """One field of a backlog row, whether it is a ``BacklogItem`` or its dict."""
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def launch_hold_view(
    life_dir: Path | str,
    *,
    daemon_alive: bool,
    backlog: list[Any] | None = None,
    continuous_enabled: bool = False,
) -> dict[str, Any] | None:
    """The hold as the UI shows it, or ``None`` when it no longer applies."""
    record = read_launch_hold(life_dir)
    if record is None or daemon_alive:
        return None
    statuses = {str(_field(item, "id") or ""): str(_field(item, "status") or "") for item in backlog or []}
    has_work = any(status in _ACTIVE_STATUSES for status in statuses.values())
    if not has_work and not (record.get("continuous") and continuous_enabled):
        return None
    held = next((item for item in backlog or [] if _field(item, "id") == record.get("item_id")), None)
    plan = (_field(held, "manager_decision") or {}).get("plan") if held is not None else None
    return {**record, "understood": _understood(Path(life_dir)), **({"plan": plan} if plan else {})}


def discard_held_work(life_dir: Path | str) -> dict[str, Any]:
    """Skip the queued, never-started work and disarm a campaign armed with it."""
    from ..daemon.state import disable_continuous_config, read_continuous_state
    from ..life.memory import LifeMemory

    root = Path(life_dir)
    skipped: list[str] = []
    backlog = LifeMemory.open(root).backlog
    for item in list(backlog.active()):
        if str(item.status) in {"pending", "parked"}:
            if backlog.update(item.id, status="skipped") is not None:
                skipped.append(str(item.id))
    try:
        if read_continuous_state(root).enabled:
            disable_continuous_config(root)
    except Exception:  # noqa: BLE001 - an unreadable state has nothing armed to undo
        pass
    clear_launch_hold(root)
    return {"skipped": skipped}
