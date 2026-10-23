"""Durable Manager steering shared by Planner and Engineer processes."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

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


@dataclass(frozen=True)
class ActiveManagerDirective:
    text: str
    source: str
    objective: str
    revision: str
    set_at: float
    version: int = _DIRECTIVE_VERSION
    authorized_objective: str = ""
    # The newest directive handed to the Planner: this one when it is, else
    # the one it replaced. Only a new one of those asks the Planner again.
    planner_revision: str = ""


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
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    try:
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise OSError("short write while appending operator steering")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _latest_ledger_objective(records: list[dict]) -> str | None:
    for record in reversed(records):
        if "objective" in record:
            return str(record.get("objective") or "")
    return None


def _sync_steering_objective(state_root: Path | str) -> list[dict]:
    """Record objective transitions in the ledger without retiring directives."""
    records = _read_steering_records(state_root)
    current = _current_objective(state_root)
    previous = _latest_ledger_objective(records)
    if previous is None:
        _append_steering_record(
            state_root,
            {
                "kind": "objective_checkpoint",
                "objective": current,
                "timestamp": _steering_timestamp(),
                "version": _STEERING_VERSION,
            },
        )
        return _read_steering_records(state_root)
    if previous != current:
        timestamp = _steering_timestamp()
        _append_steering_record(
            state_root,
            {
                "automatic": True,
                "id": uuid.uuid4().hex,
                "kind": "directive",
                "objective": current,
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
            "objective": _current_objective(state_root),
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
            "objective": _current_objective(state_root),
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


def set_active_manager_directive(
    state_root: Path | str,
    text: str,
    *,
    source: str = "manager.steer",
    authorized_objective: str = "",
    scope_objective: str | None = None,
    for_planner: bool = True,
) -> ActiveManagerDirective:
    """Replace the active directive atomically."""
    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("manager directive must not be empty")
    objective = (
        _current_objective(state_root)
        if scope_objective is None
        else str(scope_objective or "").strip()
    )
    revision = uuid.uuid4().hex
    replaced = None if for_planner else load_active_manager_directive(
        state_root, expected_objective=objective,
    )
    record = ActiveManagerDirective(
        text=normalized,
        source=str(source or "").strip() or "manager",
        objective=objective,
        revision=revision,
        set_at=time.time(),
        authorized_objective=str(authorized_objective or "").strip(),
        planner_revision=(
            revision if for_planner
            else replaced.planner_revision if replaced is not None else ""
        ),
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
        from ..daemon.state import _fsync_directory

        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    if not record.source.startswith("manager.supervision"):
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
    recorded_objective = str(payload.get("objective") or "").strip()
    authorized_objective = str(payload.get("authorized_objective") or "").strip()
    current_objective = (
        _current_objective(state_root)
        if expected_objective is None
        else str(expected_objective or "").strip()
    )
    scopes = {recorded_objective, authorized_objective} - {""}
    if (
        scopes
        and (expected_objective is not None or current_objective)
        and current_objective not in scopes
    ):
        return None
    try:
        set_at = float(payload.get("set_at") or 0.0)
        version = int(payload.get("version") or 0)
    except (TypeError, ValueError):
        return None
    if version != _DIRECTIVE_VERSION:
        return None
    return ActiveManagerDirective(
        text=text,
        source=str(payload.get("source") or "").strip() or "manager",
        objective=recorded_objective,
        revision=str(payload.get("revision") or "").strip(),
        set_at=set_at,
        authorized_objective=authorized_objective,
        version=version,
        # A directive written before this field was always the Planner's too.
        planner_revision=str(
            payload.get("planner_revision", payload.get("revision")) or ""
        ).strip(),
    )


def active_manager_directive_message(
    state_root: Path | str | None,
) -> str:
    """Render all active standing steering, newest first and budget capped."""
    return render_active_steering(state_root)


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
    "active_manager_directive_message",
    "append_steering_directive",
    "clear_active_manager_directive",
    "load_active_manager_directive",
    "record_operator_messages",
    "render_active_steering",
    "set_active_manager_directive",
]
