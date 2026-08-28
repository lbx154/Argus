"""Durable Manager steering shared by Planner and Engineer processes."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

ACTIVE_MANAGER_DIRECTIVE_FILENAME = "active_manager_directive.json"
ACTIVE_MANAGER_DIRECTIVE_PREFIX = (
    "[ACTIVE MANAGER STEERING DIRECTIVE - persists until replaced or cleared] "
)
STEERING_LEDGER_FILENAME = "STEERING.jsonl"
STEERING_HEADER = "## Operator steering (standing)"
STEERING_MAX_ENTRIES = 10
STEERING_MAX_CHARS = 4_000
_DIRECTIVE_VERSION = 1
_STEERING_VERSION = 1
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


def _steering_path(state_root: Path | str) -> Path:
    return Path(state_root) / STEERING_LEDGER_FILENAME


def _steering_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _read_steering_records(state_root: Path | str | None) -> list[dict]:
    if not state_root:
        return []
    try:
        lines = _steering_path(state_root).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        try:
            version = int(record.get("version") or 0)
        except (TypeError, ValueError):
            continue
        if version == _STEERING_VERSION:
            records.append(record)
    return records


def _append_steering_record(state_root: Path | str, record: dict) -> None:
    """Append one complete JSON record without rewriting prior steering."""
    path = _steering_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise OSError("short write while appending operator steering")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _latest_ledger_objective_sha256(records: list[dict]) -> str | None:
    for record in reversed(records):
        if "objective_sha256" in record:
            return str(record.get("objective_sha256") or "")
    return None


def _sync_steering_objective(state_root: Path | str) -> list[dict]:
    """Record objective transitions in the ledger without retiring directives."""
    records = _read_steering_records(state_root)
    current_hash = _current_objective_sha256(state_root)
    previous_hash = _latest_ledger_objective_sha256(records)
    if previous_hash is None:
        _append_steering_record(
            state_root,
            {
                "kind": "objective_checkpoint",
                "objective_sha256": current_hash,
                "timestamp": _steering_timestamp(),
                "version": _STEERING_VERSION,
            },
        )
        return _read_steering_records(state_root)
    if previous_hash != current_hash:
        timestamp = _steering_timestamp()
        _append_steering_record(
            state_root,
            {
                "automatic": True,
                "id": uuid.uuid4().hex,
                "kind": "directive",
                "objective_sha256": current_hash,
                "source": "objective.change",
                "text": f"OBJECTIVE.md changed on {timestamp[:10]}",
                "timestamp": timestamp,
                "version": _STEERING_VERSION,
            },
        )
        return _read_steering_records(state_root)
    return records


def _active_steering_records(records: list[dict]) -> list[dict]:
    active: list[dict] = []
    for record in records:
        kind = str(record.get("kind") or "")
        if kind == "directive" and str(record.get("text") or "").strip():
            active.append(record)
        elif kind == "retraction":
            retired = {
                str(value)
                for value in (record.get("retired_ids") or [])
                if str(value)
            }
            if retired:
                active = [row for row in active if str(row.get("id") or "") not in retired]
    return active


def append_steering_directive(
    state_root: Path | str,
    text: str,
    *,
    source: str = "operator.inbox",
) -> dict:
    """Append one standing directive, or an append-only retraction tombstone."""
    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("manager directive must not be empty")
    records = _sync_steering_objective(state_root)
    timestamp = _steering_timestamp()
    if normalized.casefold().startswith("retract:"):
        target = normalized.split(":", 1)[1].strip()
        active = _active_steering_records(records)
        folded = target.casefold()
        if folded in {"all", "*", "standing", "directives"}:
            matches = active
        elif folded:
            exact = [
                row for row in active
                if str(row.get("text") or "").strip().casefold() == folded
            ]
            matches = exact or [
                row for row in active
                if folded in str(row.get("text") or "").casefold()
            ]
        else:
            matches = active[-1:]
        record = {
            "kind": "retraction",
            "objective_sha256": _current_objective_sha256(state_root),
            "retired_ids": [str(row.get("id") or "") for row in matches],
            "source": str(source or "").strip() or "operator.inbox",
            "target": target,
            "timestamp": timestamp,
            "version": _STEERING_VERSION,
        }
    else:
        record = {
            "id": uuid.uuid4().hex,
            "kind": "directive",
            "objective_sha256": _current_objective_sha256(state_root),
            "source": str(source or "").strip() or "operator.inbox",
            "text": normalized,
            "timestamp": timestamp,
            "version": _STEERING_VERSION,
        }
    _append_steering_record(state_root, record)
    return record


def record_operator_messages(
    state_root: Path | str,
    messages: list[str],
    *,
    source: str = "operator.inbox",
    manager: Any = None,
    mission_id: str = "",
) -> None:
    """Persist drained operator messages without promoting all of them to standing."""
    from ..core.operator_context import (
        OperatorContextStore,
        append_directive,
        standing_sounding,
    )

    for message in messages:
        text = str(message or "").strip()
        # Manager steering is queued as the already-rendered standing block for
        # immediate one-shot delivery. Its underlying directive was appended by
        # ``set_active_manager_directive`` and must not be appended a second time.
        if not text or text.startswith(STEERING_HEADER) or text.startswith(
            ACTIVE_MANAGER_DIRECTIVE_PREFIX
        ):
            continue
        decisions: list[dict[str, Any]] = []
        if manager is not None:
            manager.classify_front_door(
                text,
                intake_sink=decisions.append,
                active_mission=True,
            )
        if decisions:
            from ..core.operator_context import IntakeDecision, persist_intake_decision

            if (
                decisions[-1].get("kind") == "credential_grant"
                and "[stored in capability vault]" in text
                and any(
                    record.type == "capability" and record.available
                    for record in OperatorContextStore(state_root).records()
                )
            ):
                continue
            persist_intake_decision(
                state_root,
                text,
                IntakeDecision(**decisions[-1]),
                source=source,
                mission_id=mission_id,
            )
            continue
        store = OperatorContextStore(state_root)
        is_standing = standing_sounding(text)
        append_directive(
            state_root,
            text,
            scope="project" if is_standing else "mission",
            lifetime="standing" if is_standing else "bounded_increment",
            applies_to_roles="all",
            source=source,
            expected_revision=store.revision,
        )


def render_active_steering(state_root: Path | str | None) -> str:
    if not state_root:
        return ""
    try:
        records = _sync_steering_objective(state_root)
    except OSError:
        # Prompt rendering is read-mostly and may be asked to probe a synthetic
        # or read-only candidate root. Existing ledger content can still render;
        # inability to create an initial checkpoint must not abort a review.
        records = _read_steering_records(state_root)
    active = _active_steering_records(records)[-STEERING_MAX_ENTRIES:]
    if not active:
        from ..core.operator_context import OperatorContextStore

        projection = OperatorContextStore(state_root).project(
            "engineer", consume_once=False
        )
        active = [
            {"text": record.text, "timestamp": record.created_at}
            for record in reversed(projection.directives[-STEERING_MAX_ENTRIES:])
        ]
    if not active:
        return ""
    directive_lines: list[str] = []
    timestamp_lines: list[str] = []

    def rendered() -> str:
        return "\n".join(
            [
                STEERING_HEADER,
                *directive_lines,
                "## Steering record timestamps",
                *timestamp_lines,
            ]
        )

    for index, record in enumerate(reversed(active), start=1):
        timestamp = str(record.get("timestamp") or "unknown time")
        text = " ".join(str(record.get("text") or "").split())
        line = f"- {text}"
        timestamp_line = f"- directive {index}: {timestamp}"
        directive_lines.append(line)
        timestamp_lines.append(timestamp_line)
        if len(rendered()) > STEERING_MAX_CHARS:
            directive_lines.pop()
            timestamp_lines.pop()
            fixed = len(rendered()) + len(timestamp_line) + 2
            remaining = STEERING_MAX_CHARS - fixed
            if remaining > 20:
                directive_lines.append(line[: remaining - 1].rstrip() + "…")
                timestamp_lines.append(timestamp_line)
            break
    return rendered()


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
    append_steering_directive(state_root, normalized, source=record.source)
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
    """Render all active standing steering, newest first and budget capped."""
    return render_active_steering(state_root)


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
    """Explicitly retire all standing directives and clear legacy metadata."""
    path = _directive_path(state_root)
    existed = bool(_active_steering_records(_read_steering_records(state_root)))
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    else:
        existed = True
    if _active_steering_records(_read_steering_records(state_root)):
        append_steering_directive(state_root, "retract: all", source="manager.clear")
    return existed


__all__ = [
    "ACTIVE_MANAGER_DIRECTIVE_FILENAME",
    "ACTIVE_MANAGER_DIRECTIVE_PREFIX",
    "STEERING_HEADER",
    "STEERING_LEDGER_FILENAME",
    "ActiveManagerDirective",
    "OperatorQuestionPolicy",
    "active_manager_directive_message",
    "active_operator_question_policy",
    "append_steering_directive",
    "clear_active_manager_directive",
    "load_active_manager_directive",
    "record_operator_messages",
    "render_active_steering",
    "set_active_manager_directive",
]
