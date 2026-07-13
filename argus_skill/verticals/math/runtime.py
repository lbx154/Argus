"""Runtime helpers used only when the persisted vertical is ``math``."""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from pathlib import Path
from typing import Any

LEAN_FORMALIZATION_KIND = "lean_formalization"
LEAN_FORMALIZATION_TAGS = (
    "planner",
    "math",
    "formalization",
    "lean_verification",
    "scope:bounded",
)


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


def enqueue_lean_formalization_task(
    project_root: Path | str,
    *,
    life_dir: Path | str,
    parent_mission_id: str,
    original_objective: str,
    step_title: str,
    step_detail: str,
) -> tuple[Any | None, bool]:
    """Create one restart-safe bounded Lean child selected by the Math Planner."""
    from ...life.memory import BacklogItem, LifeMemory
    from ...skills.vertical_select import resolve_vertical

    root = Path(project_root).expanduser()
    if resolve_vertical(root) != "math":
        return None, False
    memory = LifeMemory.open(Path(life_dir).expanduser())
    parent = next(
        (
            item
            for item in memory.backlog.all()
            if item.id == parent_mission_id
        ),
        None,
    )
    if parent is None:
        return None, False
    parent_tag = f"parent:{parent_mission_id}"
    existing = next(
        (
            item
            for item in memory.backlog.all()
            if parent_tag in item.tags and "lean_verification" in item.tags
        ),
        None,
    )
    if existing is not None:
        return existing, False

    title = str(step_title or "Formalize and verify in Lean").strip()
    detail = str(step_detail or "").strip()
    objective = (
        "Execute this independent bounded Math formalization / Lean verification "
        "subtask selected by the Planner.\n\n"
        f"Parent objective:\n{str(original_objective or '').strip()}\n\n"
        f"Planner-selected step:\n{title}"
        + (f"\n{detail}" if detail else "")
        + "\n\nRequired canonical artifacts in the project root:\n"
        "- Main.lean (canonical source). Preserve any descriptive Lean source "
        "such as DivisibilityTransitive.lean.\n"
        "- compile.log with the exact compiler and axiom-audit commands, versions, "
        "outputs, and exit codes.\n"
        "- lean_check.json from the structured checker.\n"
        "- statement_fidelity.md comparing the original and Lean statements "
        "object-by-object, quantifier-by-quantifier, hypothesis-by-hypothesis, "
        "and conclusion-by-conclusion.\n\n"
        "First author statement_fidelity.md. Then run:\n"
        "python -m argus_skill.tools.lean_check <lean-source> --lake "
        "--artifact-dir . --statement-fidelity statement_fidelity.md\n"
        "Do not report success unless all four artifacts exist and the structured "
        "check, proof-hole scan, and axiom audit succeed."
    )
    child = BacklogItem.new(
        title=f"Lean verification · {title}",
        objective=objective,
        priority=parent.priority,
        max_cost_usd=parent.max_cost_usd,
        tags=[*LEAN_FORMALIZATION_TAGS, parent_tag],
        notes=f"Planner-selected child of {parent_mission_id}",
        iterate=False,
        iteration_max_cycles=1,
        iteration_budget_usd=parent.iteration_budget_usd,
        deps=[parent_mission_id],
    )
    return memory.backlog.add(child), True


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
    "LEAN_FORMALIZATION_KIND",
    "LEAN_FORMALIZATION_TAGS",
    "append_method_ledger",
    "enqueue_lean_formalization_task",
    "load_math_adaptation_state",
    "math_adaptation_state_path",
    "math_role_banner",
    "save_math_adaptation_state",
]
