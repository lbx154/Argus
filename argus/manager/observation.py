"""Bounded, attributable project evidence for Manager conversation and supervision."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.json_codec import loads_finite_json
from ..core.scoped_file import open_regular_file
from ..core.secret_guard import known_secret_values, redact_secrets_record
from ..daemon.state import ContinuousConfigState, read_continuous_state, read_daemon_status
from .observation_projection import MAX_ITEMS, MAX_OBSERVATION_BYTES, bounded_facts, render_facts

MAX_SOURCE_BYTES = 128 * 1024
_TASK_FIELDS = (
    "id", "title", "status", "objective", "started_ts", "pending_question",
    "last_error", "plan_id", "plan_version", "deps", "acceptance_check", "goal_contribution",
)


def _semantic(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: _semantic(item) for key, item in value.items() if key not in {
            "ts", "timestamp", "created_at", "updated_at", "set_at", "observed_at", "started_ts",
            "event_id", "round", "round_index", "rejected_attempts", "age_s", "evidence_refs",
        }}
        if result.get("pending_question") and result.get("status") in {"running", "paused_operator"}:
            result["status"] = "awaiting_operator"
            result.pop("last_error", None)
        return result
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _read_object(
    path: Path, *, limitations: list[str] | None = None, unobserved: dict[str, str] | None = None,
) -> dict[str, Any]:
    raw = b""

    def signature(stat: os.stat_result) -> tuple[int, ...]:
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def unavailable(reason: str) -> dict[str, Any]:
        if limitations is not None:
            limitations.append(f"{path}: not fully observed ({reason})")
        if unobserved is not None:
            try:
                stamp: tuple[int, ...] | None = signature(path.lstat())
            except OSError:
                stamp = None
            # A bounded prefix plus filesystem identity detects changes without
            # scanning an arbitrarily large file. This is explicitly NOT a claim
            # to have observed its complete canonical contents.
            unobserved[str(path)] = _digest({"stat": stamp, "prefix_sha256": hashlib.sha256(raw).hexdigest()})
        return {}

    try:
        with open_regular_file(path) as handle:
            before = signature(os.fstat(handle.fileno()))
            raw = handle.read(MAX_SOURCE_BYTES + 1)
            after = signature(os.fstat(handle.fileno()))
        if before != after:
            return unavailable("source changed while being read")
        if len(raw) > MAX_SOURCE_BYTES:
            return unavailable(f"source exceeds the {MAX_SOURCE_BYTES}-byte read bound")
        value = loads_finite_json(raw)
        return value if isinstance(value, dict) else unavailable("source is not a JSON object")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError, RuntimeError):
        return unavailable("source could not be read as a valid JSON object")


def control_identity(
    root: Path, *, limitations: list[str] | None = None, unobserved: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Only durable authority belongs in the commit fence, not display activity."""
    from ..core.pipeline_state import read_pipeline_state

    return {
        "continuous": asdict(read_continuous_state(root)),
        "pipeline": read_pipeline_state(root),
        "campaign": _read_object(root / "campaign-control" / "HEAD.json", limitations=limitations, unobserved=unobserved),
        "directive": _read_object(root / "active_manager_directive.json", limitations=limitations, unobserved=unobserved),
    }


@dataclass(frozen=True)
class ManagerObservation:
    root: Path
    continuous: ContinuousConfigState
    control_revision: str
    evidence_revision: str
    facts: dict[str, Any]
    incomplete_requirements: tuple[str, ...] = ()

    def current(self) -> bool:
        return _digest(control_identity(self.root)) == self.control_revision

    def render(self) -> str:
        return render_facts(self.facts)


def observe_project(root: Path | str, *, event: dict[str, Any] | None = None) -> ManagerObservation:
    from ..core.mission_view import load_mission_view
    from ..life.context_packet import mission_context_dir
    from ..life.memory import Backlog, _read_jsonl_tail_history
    from ..life.role_activity import role_activity

    root = Path(root).expanduser().resolve()
    diagnostics: list[str] = []
    unobserved: dict[str, str] = {}
    source_semantics: dict[str, str] = {}
    authority = control_identity(root, limitations=diagnostics, unobserved=unobserved)
    continuous = read_continuous_state(root)

    def read_source(path: Path, *, identity: bool = True) -> dict[str, Any]:
        value = _read_object(path, limitations=diagnostics, unobserved=unobserved)
        if identity:
            source_semantics[str(path.relative_to(root))] = _digest(_semantic(value))
        return value

    daemon = read_daemon_status(root)
    active = Backlog(root / "backlog.jsonl").active()
    # Selection limits presentation and per-task artifact reads, not awareness
    # of changes to the already-loaded canonical task inventory.
    backlog_revision = _digest(_semantic([
        {name: getattr(item, name) for name in _TASK_FIELDS} for item in active
    ]))
    view = load_mission_view(root)
    events = _read_jsonl_tail_history(root / "events.jsonl", 40)
    relevant_types = {
        "life.mission.completed", "life.mission.started", "life.mission.failed",
        "life.planner.verdict", "round.review.completed", "life.manager.stage_decision",
    }
    relevant = [row for row in events if str(row.get("type") or "") in relevant_types][-8:]
    if event is not None:
        relevant.append(event)
    selected = sorted(active, key=lambda item: (item.status != "running", item.priority, item.ts))[:MAX_ITEMS]
    items = []
    refs: list[dict[str, str]] = []
    for item in selected:
        row = {name: getattr(item, name) for name in _TASK_FIELDS}
        packet = mission_context_dir(root, item.id) / "mission.json"
        if packet.exists() or packet.is_symlink():
            handoff = packet.parent / "latest.json"
            latest = read_source(handoff)
            reference = latest.get("handoff")
            if isinstance(reference, dict) and isinstance(reference.get("path"), str):
                target = Path(reference["path"])
                if target.resolve().parent == packet.parent.resolve():
                    handoff = target
                    latest = read_source(target)
            row["evidence"] = {key: latest[key] for key in (
                "kind", "engineer_summary", "review",
            ) if key in latest}
            frontier = read_source(packet.parent / "frontier.json")
            row["frontier"] = {key: frontier[key] for key in (
                "current_hypothesis", "artifacts", "evidence", "remaining_work", "active_regression",
            ) if key in frontier}
            refs.append({"path": str(packet.relative_to(root)), "observation_sha256": _digest(read_source(packet))})
            if handoff.is_file():
                refs.append({"path": str(handoff.relative_to(root)), "observation_sha256": _digest(row["evidence"])})
            if (packet.parent / "frontier.json").is_file():
                refs.append({"path": str((packet.parent / "frontier.json").relative_to(root)), "observation_sha256": _digest(row["frontier"])})
        items.append(row)
    refs.append({"path": "backlog.jsonl", "observation_sha256": backlog_revision})
    refs.append({"path": "mission-view.json", "observation_sha256": _digest(_semantic({
        key: view.get(key) for key in ("review", "stage", "frontier", "delivery")
    }))})
    for reference in refs:
        missing_signature = unobserved.get(str(root / reference["path"]))
        if missing_signature:
            reference.pop("observation_sha256", None)
            reference["unobserved_source_signature"] = missing_signature
    if len(active) > len(selected):
        diagnostics.append(f"showing {len(selected)} of {len(active)} active tasks")
    # Archived completions remain visible through their canonical event and
    # current projection even when there are no live backlog rows.
    event_fields = ("event_id", "type", "ts", "item_id", "title", "status", "summary", "reason", "success", "outcome", "agent_layer", "round_index")
    event_rows = [{key: row[key] for key in event_fields if key in row} for row in relevant]
    facts: dict[str, Any] = {
        "objective": continuous.objective,
        "continuous_enabled": continuous.enabled,
        "daemon": {"alive": daemon.alive, "health": daemon.health_state},
        "tasks": items,
        "roles": {name: asdict(state) for name, state in role_activity(root).items()},
        "stage": view.get("stage", {}),
        "review": view.get("review", {}),
        "frontier": view.get("frontier", {}),
        "delivery": view.get("delivery"),
        "recent_events": event_rows,
        "evidence_refs": refs,
        "limitations": diagnostics,
    }
    supervision = read_source(root / "manager-supervision" / "latest.json", identity=False)
    facts["manager_supervision"] = {key: supervision[key] for key in (
        "id", "status", "decision", "effects", "issued_at", "applied_at",
    ) if key in supervision}
    try:
        from ..advisor.receipts import recent_receipts

        facts["advisor_consultations"] = [
            {key: receipt[key] for key in (
                "consultation_id", "status", "mission_id", "answer", "evidence_refs",
            ) if key in receipt}
            for receipt in recent_receipts(root, limit=3) if receipt.get("status") == "completed"
        ]
    except (ImportError, OSError, sqlite3.Error):
        facts["advisor_consultations"] = []
    # The observation's identity excludes its collection time and volatile role
    # ages. Hash complete canonical semantics BEFORE any presentation excerpt,
    # budget omission, or secret redaction can hide a meaningful source change.
    revision = _digest({
        **_semantic({key: value for key, value in facts.items()
                     if key not in {"roles", "daemon", "recent_events", "evidence_refs", "continuous_enabled", "manager_supervision"}}),
        "daemon_health": facts["daemon"]["health"],
        "backlog_revision": backlog_revision,
        "source_semantics": source_semantics,
        "unobserved_source_signatures": unobserved,
    })
    facts["observed_at"] = time.time()
    facts["evidence_revision"] = revision
    facts, incomplete = bounded_facts(redact_secrets_record(facts, known_values=known_secret_values()))
    return ManagerObservation(root, continuous, _digest(authority), revision, facts,
                              tuple(dict.fromkeys([*incomplete, *unobserved])))


__all__ = ["MAX_OBSERVATION_BYTES", "ManagerObservation", "observe_project", "control_identity"]
