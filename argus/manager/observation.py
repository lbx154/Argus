"""Bounded, attributable project evidence for Manager conversation and supervision."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.json_codec import loads_finite_json
from ..core.scoped_file import open_regular_file
from ..core.secret_guard import known_secret_values, redact_secrets_record
from ..core.workspace_facts import fact_text
from ..daemon.state import ContinuousConfigState, read_continuous_state, read_daemon_status
from .observation_projection import MAX_ITEMS, MAX_OBSERVATION_BYTES, bounded_facts, render_facts

MAX_SOURCE_BYTES = 128 * 1024
_TASK_FIELDS = (
    "id", "title", "status", "objective", "started_ts", "pending_question",
    "last_error", "plan_id", "plan_version", "deps", "acceptance_check", "goal_contribution",
)


def _read_object(
    path: Path, *, limitations: list[str] | None = None, unobserved: dict[str, str] | None = None,
) -> dict[str, Any]:
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
            # Filesystem identity detects changes without scanning an
            # arbitrarily large file. This is explicitly NOT a claim to have
            # observed its complete canonical contents.
            unobserved[str(path)] = fact_text(stamp)
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


def control_identity(root: Path, *, limitations: list[str] | None = None) -> dict[str, Any]:
    """Only durable authority belongs in the commit fence, not display activity."""
    from ..core.pipeline_state import read_pipeline_state

    return {
        "continuous": asdict(read_continuous_state(root)),
        "pipeline": read_pipeline_state(root),
        "campaign": _read_object(root / "campaign-control" / "HEAD.json", limitations=limitations),
        "directive": _read_object(root / "active_manager_directive.json", limitations=limitations),
    }


@dataclass(frozen=True)
class ManagerObservation:
    root: Path
    continuous: ContinuousConfigState
    control_revision: str
    facts: dict[str, Any]

    def current(self) -> bool:
        return fact_text(control_identity(self.root)) == self.control_revision

    def render(self) -> str:
        return render_facts(self.facts)


def observe_project(root: Path | str, *, event: dict[str, Any] | None = None) -> ManagerObservation:
    from ..core.mission_view import load_mission_view
    from ..life.context_packet import mission_context_dir
    from ..life.memory import Backlog, _read_jsonl_tail_history, operator_answers
    from ..life.role_activity import role_activity

    root = Path(root).expanduser().resolve()
    diagnostics: list[str] = []
    authority = control_identity(root, limitations=diagnostics)
    continuous = read_continuous_state(root)

    def read_source(path: Path) -> dict[str, Any]:
        return _read_object(path, limitations=diagnostics)

    daemon = read_daemon_status(root)
    backlog = Backlog(root / "backlog.jsonl")
    active = backlog.active()
    view = load_mission_view(root)
    events = _read_jsonl_tail_history(root / "events.jsonl", 40)
    relevant_types = {
        "life.mission.completed", "life.mission.started", "life.mission.failed",
        "life.planner.verdict", "round.review.completed", "life.manager.stage_decision",
        "life.runtime.incident.escalated",
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
            read_source(packet)  # only to name an unreadable packet among the limitations
            refs.append({"path": str(packet.relative_to(root))})
            if handoff.is_file():
                refs.append({"path": str(handoff.relative_to(root))})
        items.append(row)
    refs.append({"path": "backlog.jsonl"})
    # The stage, review and delivery above are computed from this log.
    refs.append({"path": "events.jsonl"})
    if len(active) > len(selected):
        diagnostics.append(f"showing {len(selected)} of {len(active)} active tasks")
    # Archived completions remain visible through their canonical event and
    # current projection even when there are no live backlog rows.
    event_fields = (
        "event_id",
        "type",
        "ts",
        "item_id",
        "title",
        "status",
        "summary",
        "reason",
        "verification_obstacle",
        "verification_obstacle_basis",
        "verification_obstacle_basis_source",
        "residual_risk",
        "success",
        "outcome",
        "agent_layer",
        "round_index",
        "incident_id",
        "detector",
        "invariant",
        "subject_kind",
        "subject_id",
        "severity",
        "recovery_action",
        "verification",
        "manager_attention_required",
    )
    event_rows = [{key: row[key] for key in event_fields if key in row} for row in relevant]
    facts: dict[str, Any] = {
        "objective": continuous.objective,
        # Asked after the objective was written, the operator's answers are
        # its later word wherever the two disagree.
        "operator_answers": operator_answers(backlog, continuous.objective),
        "continuous_enabled": continuous.enabled,
        "daemon": {"alive": daemon.alive, "health": daemon.health_state},
        "tasks": items,
        "roles": {name: asdict(state) for name, state in role_activity(root).items()},
        "stage": view.get("stage", {}),
        "review": view.get("review", {}),
        "delivery": view.get("delivery"),
        "recent_events": event_rows,
        "evidence_refs": refs,
        "limitations": diagnostics,
    }
    from ..core.autonomy import read_autonomous_assumptions
    from ..core.residual_risk import residual_risk_records

    # Every reading already chosen in the operator's place this run, in the
    # one place it is kept: a decision that restates one would list it twice.
    facts["recorded_assumptions"] = [
        {key: row.get(key) for key in ("item_id", "source", "conflict", "reading")}
        for row in read_autonomous_assumptions(root)
    ]

    # Every acceptance of a check left unverified, with its id so the Manager
    # can revoke one; a revoked one stays listed as revoked.
    facts["accepted_residual_risks"] = [
        {key: row.get(key) for key in ("id", "item_id", "check", "risk", "accepted_by", "basis", "basis_source")}
        | {"revoked": bool(row.get("revoked_at"))}
        for row in residual_risk_records(root)[-16:]
    ]
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
    facts["observed_at"] = time.time()
    facts = bounded_facts(redact_secrets_record(facts, known_values=known_secret_values()))
    return ManagerObservation(root, continuous, fact_text(authority), facts)


__all__ = ["MAX_OBSERVATION_BYTES", "ManagerObservation", "observe_project", "control_identity"]
