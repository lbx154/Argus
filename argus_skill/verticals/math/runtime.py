"""Runtime helpers used only when the persisted vertical is ``math``."""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from pathlib import Path
from typing import Any


def math_role_banner(project_root: Path | str, role: str) -> str:
    """Resolve the Math banner without changing any other vertical's prompt."""
    try:
        from ...skills.vertical_select import resolve_vertical
        from .._base import load_vertical, vertical_role_banner

        root = Path(project_root).expanduser()
        if resolve_vertical(root) != "math":
            return ""
        module = load_vertical("math", project_root=root)
        return vertical_role_banner(module, role)
    except Exception:  # noqa: BLE001 — role guidance is fail-open
        return ""


def append_method_ledger(
    project_root: Path | str,
    record: dict[str, Any],
) -> Path:
    """Append one auditable Math strategy-adaptation record."""
    root = Path(project_root).expanduser()
    path = root / "research" / "MATH_METHOD_LEDGER.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": time.time(), **record}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    return path


def math_adaptation_state_path(
    checkpoint_path: Path | str,
    mission_id: str,
) -> Path:
    """Return a project-state path isolated to one stable mission ID."""
    checkpoint = Path(checkpoint_path).expanduser()
    key = hashlib.sha256(mission_id.encode("utf-8")).hexdigest()
    return checkpoint.parent / "math_adaptation" / f"{key}.json"


def load_math_adaptation_state(path: Path | str, mission_id: str) -> dict[str, Any]:
    """Load and validate persisted adaptation limits for one resumed mission."""
    state_path = Path(path).expanduser()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "trigger_count": 0,
            "spent_usd": 0.0,
            "rejection_streak": [],
            "method_records": [],
        }
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"invalid Math adaptation state: {state_path}")
    if payload.get("mission_id") != mission_id:
        raise ValueError(f"Math adaptation mission mismatch: {state_path}")
    trigger_count = payload.get("trigger_count")
    spent_usd = payload.get("spent_usd")
    rejection_streak = payload.get("rejection_streak")
    method_records = payload.get("method_records")
    valid_rejections = (
        isinstance(rejection_streak, list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("round_index"), int)
            and not isinstance(item.get("round_index"), bool)
            and item["round_index"] > 0
            and isinstance(item.get("reason"), str)
            and isinstance(item.get("next_action"), str)
            and _is_finite_json(item)
            for item in rejection_streak
        )
    )
    valid_records = (
        isinstance(method_records, list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("status"), str)
            and bool(item["status"])
            and isinstance(item.get("trigger_index"), int)
            and not isinstance(item.get("trigger_index"), bool)
            and item["trigger_index"] >= 0
            and _is_finite_json(item)
            for item in method_records
        )
    )
    if (
        isinstance(trigger_count, bool)
        or not isinstance(trigger_count, int)
        or trigger_count < 0
        or not _is_finite_nonnegative_number(spent_usd)
        or not valid_rejections
        or not valid_records
    ):
        raise ValueError(f"invalid Math adaptation counters: {state_path}")
    return {
        "trigger_count": trigger_count,
        "spent_usd": float(spent_usd),
        "rejection_streak": [dict(item) for item in rejection_streak],
        "method_records": [dict(item) for item in method_records],
    }


def save_math_adaptation_state(
    path: Path | str,
    mission_id: str,
    *,
    trigger_count: int,
    spent_usd: float,
    rejection_streak: list[dict[str, Any]],
    method_records: list[dict[str, Any]],
) -> Path:
    """Atomically persist restart-safe Scientist limits and rejection evidence."""
    state_path = Path(path).expanduser()
    if not _is_finite_nonnegative_number(spent_usd):
        raise ValueError("Math adaptation spend must be finite and non-negative")
    if not _is_finite_json(rejection_streak) or not _is_finite_json(method_records):
        raise ValueError("Math adaptation state must contain finite JSON values")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "mission_id": mission_id,
        "trigger_count": max(0, int(trigger_count)),
        "spent_usd": max(0.0, float(spent_usd)),
        "rejection_streak": rejection_streak,
        "method_records": method_records,
    }
    temporary = state_path.with_name(
        f".{state_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(state_path)
    finally:
        temporary.unlink(missing_ok=True)
    return state_path


def _is_finite_json(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_finite_json(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_finite_json(item)
            for key, item in value.items()
        )
    return False


def _is_finite_nonnegative_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        return False
    return math.isfinite(normalized) and normalized >= 0


__all__ = [
    "append_method_ledger",
    "load_math_adaptation_state",
    "math_adaptation_state_path",
    "math_role_banner",
    "save_math_adaptation_state",
]
