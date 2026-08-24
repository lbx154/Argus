"""Durable Manager steering shared by Planner and Engineer processes."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

ACTIVE_MANAGER_DIRECTIVE_FILENAME = "active_manager_directive.json"
ACTIVE_MANAGER_DIRECTIVE_PREFIX = (
    "[ACTIVE MANAGER STEERING DIRECTIVE - persists until replaced or cleared] "
)
_DIRECTIVE_VERSION = 1
OperatorQuestionPolicy = Literal["allow", "forbid", "unchanged"]
_OPERATOR_QUESTION_POLICIES = frozenset({"allow", "forbid", "unchanged"})


@dataclass(frozen=True)
class ActiveManagerDirective:
    text: str
    source: str
    objective_sha256: str
    revision: str
    set_at: float
    version: int = _DIRECTIVE_VERSION
    operator_question_policy: OperatorQuestionPolicy = "unchanged"
    authorized_objective: str = ""


def _directive_path(state_root: Path | str) -> Path:
    return Path(state_root) / ACTIVE_MANAGER_DIRECTIVE_FILENAME


def _current_objective_sha256(state_root: Path | str) -> str:
    try:
        payload = json.loads(
            (Path(state_root) / "continuous.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    objective = str(payload.get("objective") or "").strip()
    if not objective:
        return ""
    return hashlib.sha256(objective.encode("utf-8")).hexdigest()


def _current_objective(state_root: Path | str) -> str:
    try:
        payload = json.loads(
            (Path(state_root) / "continuous.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("objective") or "").strip()


def _validated_operator_question_policy(value: object) -> OperatorQuestionPolicy:
    normalized = str(value or "").strip().lower()
    if normalized in _OPERATOR_QUESTION_POLICIES:
        return cast(OperatorQuestionPolicy, normalized)
    return "unchanged"


def set_active_manager_directive(
    state_root: Path | str,
    text: str,
    *,
    source: str = "manager.steer",
    operator_question_policy: OperatorQuestionPolicy = "unchanged",
    authorized_objective: str = "",
    scope_objective: str | None = None,
) -> ActiveManagerDirective:
    """Replace the active directive atomically."""
    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("manager directive must not be empty")
    question_policy = _validated_operator_question_policy(operator_question_policy)
    if question_policy == "unchanged":
        current = load_active_manager_directive(
            state_root,
            expected_objective=scope_objective,
        )
        if current is not None:
            question_policy = current.operator_question_policy
    scoped_objective = str(scope_objective or "").strip()
    objective_sha256 = (
        _current_objective_sha256(state_root)
        if scope_objective is None
        else (
            hashlib.sha256(scoped_objective.encode("utf-8")).hexdigest()
            if scoped_objective
            else ""
        )
    )
    record = ActiveManagerDirective(
        text=normalized,
        source=str(source or "").strip() or "manager",
        objective_sha256=objective_sha256,
        revision=uuid.uuid4().hex,
        set_at=time.time(),
        operator_question_policy=question_policy,
        authorized_objective=str(authorized_objective or "").strip(),
    )
    path = _directive_path(state_root)
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    asdict(record),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    return record


def load_active_manager_directive(
    state_root: Path | str | None,
    *,
    expected_objective: str | None = None,
) -> ActiveManagerDirective | None:
    """Load the current-objective directive without consuming it."""
    if not state_root:
        return None
    try:
        payload = json.loads(
            _directive_path(state_root).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    text = str(payload.get("text") or "").strip()
    if not text:
        return None
    recorded_objective = str(payload.get("objective_sha256") or "").strip()
    authorized_objective = str(payload.get("authorized_objective") or "").strip()
    current_objective = (
        _current_objective(state_root)
        if expected_objective is None
        else str(expected_objective or "").strip()
    )
    current_objective_sha256 = (
        hashlib.sha256(current_objective.encode("utf-8")).hexdigest()
        if current_objective
        else ""
    )
    objective_scope_matches = bool(
        (recorded_objective and current_objective_sha256 == recorded_objective)
        or (
            authorized_objective
            and current_objective == authorized_objective
        )
    )
    if (
        (recorded_objective or authorized_objective)
        and (expected_objective is not None or current_objective)
        and not objective_scope_matches
    ):
        return None
    try:
        set_at = float(payload.get("set_at") or 0.0)
        version = int(payload.get("version") or 0)
    except (TypeError, ValueError):
        return None
    if version != _DIRECTIVE_VERSION:
        return None
    question_policy = _validated_operator_question_policy(
        payload.get("operator_question_policy") or "unchanged"
    )
    return ActiveManagerDirective(
        text=text,
        source=str(payload.get("source") or "").strip() or "manager",
        objective_sha256=recorded_objective,
        revision=str(payload.get("revision") or "").strip(),
        set_at=set_at,
        operator_question_policy=question_policy,
        authorized_objective=authorized_objective,
        version=version,
    )


def active_manager_directive_message(
    state_root: Path | str | None,
) -> str:
    record = load_active_manager_directive(state_root)
    if record is None:
        return ""
    return ACTIVE_MANAGER_DIRECTIVE_PREFIX + record.text


def active_operator_question_policy(
    state_root: Path | str | None,
    *,
    expected_objective: str | None = None,
) -> OperatorQuestionPolicy:
    """Read the current directive's structured operator-question policy."""
    record = load_active_manager_directive(
        state_root,
        expected_objective=expected_objective,
    )
    return record.operator_question_policy if record is not None else "unchanged"


def clear_active_manager_directive(state_root: Path | str) -> bool:
    """Clear the directive explicitly; return whether one existed."""
    path = _directive_path(state_root)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


__all__ = [
    "ACTIVE_MANAGER_DIRECTIVE_FILENAME",
    "ACTIVE_MANAGER_DIRECTIVE_PREFIX",
    "ActiveManagerDirective",
    "OperatorQuestionPolicy",
    "active_manager_directive_message",
    "active_operator_question_policy",
    "clear_active_manager_directive",
    "load_active_manager_directive",
    "set_active_manager_directive",
]
