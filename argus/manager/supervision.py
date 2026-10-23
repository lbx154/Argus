"""Evidence-triggered Manager judgments with durable, applied control receipts."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import CancelledError
from functools import partial
from inspect import signature
from pathlib import Path
from typing import Any, Callable

import portalocker

from ..core.autonomy import record_assumptions
from ..core.event_catalog import EventType
from ..core.handoff import HandOffActions
from ..core.models import RunnerOptions
from ..core.role_actions import role_actions
from ..core.run_gateway import run_exec, run_interrupt_scope
from ..daemon.state import _fsync_directory, compare_and_swap_continuous_config
from ._helpers import _manager_backend_failure
from ._session_ops import (
    _ManagerSession,
    clear_manager_pipeline_yield,
    request_manager_pipeline_yield,
)
from .observation import (
    ManagerObservation,
    _read_object,
    control_identity,
    observe_project,
)
from .supervision_errors import _failure_reason, _provider_failure_metadata

LOG = logging.getLogger(__name__)
_ADMISSION = threading.BoundedSemaphore(2)
_WORKERS: dict[str, tuple[threading.Thread, threading.Event]] = {}
_PENDING: dict[str, tuple[Any, Path | str, dict[str, Any]]] = {}
_GUARD = threading.Lock()
_CLOSED = False
_STOPPED_ROOTS: set[str] = set()
MAX_PENDING_PROJECTS = 64
_CHANGED_PATHS_SHOWN = 20


class SupervisionBusy(RuntimeError):
    """The daemon holds its control lock, usually because it is running the
    mission this decision concerns. The issued receipt is delivered at the
    next guidance boundary; this is not a failure of the decision."""


class SupervisionSuperseded(RuntimeError):
    pass


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    if path.name != "latest.json":
        _write(path.parent / "latest.json", record)


def _read(path: Path) -> dict[str, Any]:
    return _read_object(path)


def _latest_record(root: Path) -> dict[str, Any]:
    latest = _read(root / "manager-supervision" / "latest.json")
    identity = str(latest.get("id") or "")
    if re.fullmatch(r"[0-9a-f]{32}", identity):
        durable = _read(root / "manager-supervision" / f"{identity}.json")
        if durable.get("id") == identity:
            return durable
    return latest


def _short_error(exc: BaseException) -> str:
    # Only checks written here have messages safe to persist; provider and
    # runtime exception text can carry raw bodies or credentials.
    if isinstance(exc, SupervisionDecisionError):
        return " ".join(str(exc).split())[:300]
    return type(exc).__name__


def _emit(record: dict[str, Any], root: Path, phase: str) -> None:
    from ..life.event_log import JsonlEventSink

    decision = record.get("decision") or {}
    reason = record.get("failure_reason") if phase == "failed" else decision.get("reason")
    reason = reason or "Manager could not complete the evidence check; prior controls remain authoritative."
    payload = {
        "type": {
            "issued": EventType.LIFE_MANAGER_SUPERVISION_ISSUED,
            "applied": EventType.LIFE_MANAGER_SUPERVISION_APPLIED,
            "failed": EventType.LIFE_MANAGER_SUPERVISION_FAILED,
        }[phase], "agent_layer": "manager",
        "supervision_id": record["id"],
        "trigger_type": record["trigger"].get("type", ""),
        "item_id": record["trigger"].get("item_id", ""),
        "status": record["status"], "action": decision.get("action", ""),
        "reason": reason, "summary": reason, "evidence_refs": record["evidence_refs"],
        "call_id": record.get("call_id") or "", "effects": record.get("effects", {}),
        "consultation_id": decision.get("consultation_id", ""),
        "advisor_disposition": decision.get("advisor_disposition", ""),
    }
    if phase == "failed":
        payload.update({key: record[key] for key in (
            "failure_stage", "stop_kind", "error_code", "backend_exit_code",
        ) if key in record})
        if record.get("error"):
            payload["error_type"] = record["error"]
        # Every failure says what went wrong in a short message; the code stays
        # absent only where nothing classified the provider failure.
        payload["error_message"] = record.get("error_message") or record.get("error") or "unknown error"
    JsonlEventSink(None, life_dir=root).append(payload)


def mission_stop_reason(root: Path | str | None, mission_id: str | None) -> str:
    """Why the Manager stopped this run of the mission, or "" when it did not."""
    if root is None or not mission_id:
        return ""
    record = _latest_record(Path(root))
    if (
        record.get("status") != "applied"
        or record.get("decision", {}).get("action") != "stop"
        or record.get("trigger", {}).get("item_id") != mission_id
        or record.get("effects", {}).get("stopped_attempt") != _attempt_key(Path(root), mission_id)
    ):
        return ""
    return record["decision"]["reason"]


class SupervisionDecisionError(ValueError):
    """A Manager reply that could not become a decision, with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _manager_holds_operator_authority(root: Path | str | None = None) -> bool:
    """The Manager decides in the operator's place whenever they cannot be asked.

    That is a run with no operator, and equally a run whose question policy
    forbids asking: the operator decides only while they can be asked, so a
    decision never has no decider. ``reviewer.tools`` reads the same rule.
    """
    from ..core.autonomy import operator_questions_can_be_asked

    return not operator_questions_can_be_asked(root)


def _check(
    payload: dict[str, Any], *, root: Path, event: dict[str, Any], observation: ManagerObservation,
) -> dict[str, Any]:
    """The decision a supervise call records, or a refusal the Manager can act on."""
    available = {ref["path"] for ref in observation.facts["evidence_refs"]}
    cited = list(dict.fromkeys(payload["evidence_refs"]))
    outside = [ref for ref in cited if ref not in available]
    if outside:
        raise ValueError(
            f"evidence_refs: {len(outside)} of {len(cited)} are not paths listed under "
            "evidence_refs below; cite only those."
        )
    to, note = payload["to"], payload.get("note", "").strip()
    if to == "planner" and not note:
        raise ValueError("to=planner needs your instruction for the Planner in note.")
    if to == "planner" and event.get("type") == EventType.LIFE_OBSERVER_NOTE:
        raise ValueError(
            "An Observer's remark never moves the work to the Planner: hand it "
            "to=team, with your instruction in note."
        )
    consultation = payload.get("consultation", {})
    if consultation:
        from ..advisor.receipts import read_receipt

        advice = read_receipt(root, consultation["id"])
        if not advice or advice.get("status") != "completed":
            raise ValueError("consultation.id names no completed advisor consultation.")
    risk = payload.get("risk", {})
    if "risk" in payload and len(risk) != 1:
        raise ValueError("risk takes either accept or revoke.")
    if "accept" in risk and not _manager_holds_operator_authority(root):
        raise ValueError("The operator can be asked, so accepting a residual risk is theirs to decide.")
    return {
        "action": "stop" if to == "stop" else "steer" if note else "continue",
        "reason": payload["summary"], "directive": note,
        # to=planner: the directive is new planning, so the Planner is asked;
        # to=operator: the task handed here waits on their answer.
        "handoff": to,
        "question": payload.get("ask", {}).get("question", ""),
        "options": payload.get("ask", {}).get("options", []),
        "cited_refs": cited,
        "consultation_id": consultation.get("id", ""),
        "advisor_disposition": consultation.get("disposition", ""),
        "risk_decision": (
            {"action": "accept", **risk["accept"]} if "accept" in risk
            else {"action": "revoke", "risk_id": risk["revoke"]} if risk else {}
        ),
        "assumptions": payload["assumptions"],
    }


def _prompt(observation: ManagerObservation, consult_reason: str = "") -> str:
    from ..core.mission_view import load_mission_view, session_is_chinese
    from ..roles.prompts.voice import operator_language

    asked = f"You are consulted now because of: {consult_reason}.\n" if consult_reason else ""
    root = getattr(observation, "root", None)
    # The summary is shown to the operator as written, like the Observer's remark.
    language = operator_language(root is not None and session_is_chinese(load_mission_view(root)))
    return (
        "You are the persistent project Manager.\n"
        + asked
        + "Judge from the evidence below whether the team's course serves the operator's "
        f"objective, and end your turn with hand_off, its summary {language}.\n"
        "Each reading in recorded_assumptions is already shown to the operator; your "
        "assumptions name only a reading this decision chooses for the first time.\n\n"
        + observation.render()
    )


def _owns_reserved_control(root: Path, record: dict[str, Any]) -> bool:
    reserved = record.get("reserved_authority")
    if not isinstance(reserved, dict):
        return False
    current = control_identity(root)
    if current == reserved:
        return True
    # The directive write can precede its outbox checkpoint. Its stable source
    # proves ownership of this one effect; all other authority must still match.
    directive = current.get("directive", {})
    return bool(
        directive.get("source") == f"manager.supervision:{record['id']}"
        and {key: value for key, value in current.items() if key != "directive"}
        == {key: value for key, value in reserved.items() if key != "directive"}
    )


def _obstacle_basis(event: dict[str, Any], observation: Any, item_id: str) -> tuple[str, str]:
    """The Reviewer's quoted statement that makes this item's check impossible here, and its source.

    Only a review of this same item counts: a basis quoted for another item,
    or by a review that names no item, grounds nothing here.
    """
    if not item_id:
        return "", ""
    rows = [event, *reversed(list((getattr(observation, "facts", None) or {}).get("recent_events") or []))]
    for row in rows:
        if not isinstance(row, dict) or str(row.get("item_id") or "") != item_id:
            continue
        basis = " ".join(str(row.get("verification_obstacle_basis") or "").split())
        if basis:
            return basis, " ".join(str(row.get("verification_obstacle_basis_source") or "").split())[:300]
    return "", ""


def _apply_risk_decision(
    root: Path, record: dict[str, Any], event: dict[str, Any], observation: Any,
    effects: dict[str, Any],
) -> None:
    """Record the Manager's acceptance or revocation of one check's residual risk.

    An acceptance holds only in a run with no operator, and only for a check a
    Reviewer grounded with a quoted statement. Idempotent per decision, so a
    replayed delivery records it once.
    """
    from ..core.residual_risk import accept_residual_risk, describe, revoke_residual_risk

    risk = record["decision"].get("risk_decision") or {}
    source = f"manager.supervision:{record['id']}"
    if risk.get("action") == "revoke":
        row = revoke_residual_risk(
            root, risk.get("risk_id", ""), revoked_by=source, reason=record["decision"].get("reason", ""),
        )
        effects["residual_risk_revoked"] = row["id"] if row else ""
        return
    if risk.get("action") != "accept":
        return
    if not _manager_holds_operator_authority(root):
        effects["residual_risk_refused"] = "the operator can be asked; accepting a residual risk is theirs"
        return
    item_id = str((record.get("trigger") or {}).get("item_id") or "")
    if not item_id:
        effects["residual_risk_refused"] = "the decision names no item; an acceptance covers one item's check"
        return
    basis, basis_source = _obstacle_basis(event, observation, item_id)
    if not basis:
        effects["residual_risk_refused"] = "no Reviewer of this item quoted a statement making this check impossible here"
        return
    entry = accept_residual_risk(
        root, check=risk.get("check", ""), risk=risk.get("risk", ""), accepted_by="manager",
        source_ref=source, item_id=item_id, basis=basis, basis_source=basis_source,
    )
    if entry is not None:
        effects["residual_risk_accepted"] = f"[{entry['id']}] {describe(entry)}"
        effects["residual_risk_basis_source"] = basis_source or "task"


def _put_to_operator(root: Path, record: dict[str, Any], question: str) -> str:
    """Park the task handed to the Manager on the operator's answer, on its question card.

    A task still settling is delivered to at the next boundary; the question
    the card already holds is not asked twice.
    """
    from ..life.event_log import JsonlEventSink
    from ..life.memory import Backlog
    from ..life.supervisor.pending_notify import ask_operator

    item_id = str(record.get("trigger", {}).get("item_id") or "")
    backlog = Backlog(root / "backlog.jsonl")
    task = next((row for row in backlog.active() if row.id == item_id), None) if item_id else None
    if task is None:
        raise SupervisionSuperseded("the task the question is about has ended")
    if task.status == "running":
        raise SupervisionBusy("the task the question is about is still settling")
    if task.pending_question != question:
        ask_operator(
            root, task, question, reason=record["decision"]["reason"], layer="manager",
            save=partial(backlog.update, task.id), emit=JsonlEventSink(None, life_dir=root).append,
            options=record["decision"].get("options", ()),
        )
    return task.id


#: Who put the work in the Manager's hands, by the source of the round's record.
HANDED_TO_MANAGER = {
    "engineer_handoff": "the Engineer handed the work to you",
    "reviewer": "the Reviewer handed the work to you",
    "unchanged_return": "the work came back unchanged and climbed to you",
}


def _apply(
    root: Path, event: dict[str, Any], record: dict[str, Any],
    *, cancelled: Callable[[], bool],
) -> dict[str, Any]:
    from ..daemon.commands import daemon_command_execution_lock
    from .directive import load_active_manager_directive, set_active_manager_directive

    decision = record["decision"]
    path = root / "manager-supervision" / f"{record['id']}.json"
    yield_token = request_manager_pipeline_yield(root, cancelled=cancelled)
    try:
        # Steering is already a supported running-mission control. It must not
        # wait for the daemon's whole-mission pipeline lock; the next guidance
        # boundary consumes the inbox. No stage, DAG, or acceptance file is edited.
        with daemon_command_execution_lock(root, blocking=False) as acquired:
            if not acquired:
                raise SupervisionBusy("daemon control is busy")
            observation = observe_project(root, event=event)
            if observation.incomplete_requirements:
                record["observation_limitations"] = observation.facts["limitations"]
                record["incomplete_requirements"] = list(observation.incomplete_requirements)
                raise SupervisionSuperseded("required project facts could not be fully observed")
            if cancelled() or not (
                observation.control_revision == record["control_revision"]
                or _owns_reserved_control(root, record)
            ):
                raise SupervisionSuperseded("newer project control")
            # Work that moved on while the Manager decided does not void its
            # decision; voided, the decision was lost and nobody asked again.
            record["evidence_moved"] = observation.evidence_revision != record["evidence_revision"]
            effects = record.setdefault("effects", {"supervision_id": record["id"]})
            # Each reading the Manager chose in the operator's place, once per decision.
            record_assumptions(
                root, decision["assumptions"], item_id=str(record["trigger"].get("item_id") or ""),
                source="supervise",
            )
            if decision.get("question"):
                effects["question_item_id"] = _put_to_operator(root, record, decision["question"])
            if decision["action"] == "continue":
                effects["effect"] = "current course retained"
                _apply_risk_decision(root, record, event, observation, effects)
                record["applied_control_revision"] = observation.control_revision
                return effects
            if decision["action"] == "stop":
                # The mission's own round loop parks this run at its next
                # boundary (mission_stop_reason); its workspace stays.
                attempt = _attempt_key(root, str(record.get("trigger", {}).get("item_id") or ""))
                if not attempt:
                    raise SupervisionSuperseded("no mission run to stop")
                effects.update(effect="mission parked", stopped_attempt=attempt)
                record["applied_control_revision"] = observation.control_revision
                return effects
            if not _owns_reserved_control(root, record):
                # Reserve before any team-facing write. A crash before the
                # reservation receipt is durable is conservatively superseded;
                # generation equality alone never proves this command owns it.
                before = control_identity(root)
                continuous = observation.continuous
                swapped = compare_and_swap_continuous_config(
                    root, expected=continuous, enabled=continuous.enabled,
                    objective=continuous.objective, open_ended=continuous.open_ended,
                    done_reason=continuous.done_reason,
                )
                if not swapped:
                    raise SupervisionSuperseded("supervision generation could not be reserved")
                reserved = control_identity(root)
                expected = {**before["continuous"], "generation": continuous.generation + 1}
                expected["done_at"] = reserved["continuous"]["done_at"]
                if reserved != {**before, "continuous": expected}:
                    raise SupervisionSuperseded("newer control after reservation")
                record["reserved_authority"] = reserved
                effects["continuous_generation"] = continuous.generation + 1
                _write(path, record)
            if cancelled() or not _owns_reserved_control(root, record):
                raise SupervisionSuperseded("control changed before directive delivery")
            if decision["action"] == "steer":
                source = f"manager.supervision:{record['id']}"
                directive = load_active_manager_directive(root, expected_objective=observation.continuous.objective)
                if directive is None or directive.source != source:
                    directive = set_active_manager_directive(
                        root, decision["directive"], source=source,
                        scope_objective=observation.continuous.objective,
                        for_planner=decision["handoff"] == "planner",
                    )
                effects.update(directive_revision=directive.revision, directive_delivered=True, inbox_queued=False)
                _apply_risk_decision(root, record, event, observation, effects)
            record["applied_control_revision"] = observe_project(root, event=event).control_revision
            _write(path, record)
            return effects
    finally:
        clear_manager_pipeline_yield(root, yield_token)


def _deliver(
    root: Path, event: dict[str, Any], record: dict[str, Any], cancelled: Callable[[], bool],
    *, interruption_code: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """Replay a durable issued decision without asking the model again."""
    path = root / "manager-supervision" / f"{record['id']}.json"
    try:
        record["effects"] = _apply(root, event, record, cancelled=cancelled)
        record["status"] = "applied"
        record["applied_at"] = time.time()
        record["completed_at"] = time.time()
        for key in ("failure_reason", "failure_stage", "error", "error_type", "error_code", "error_message", "stop_kind"):
            record.pop(key, None)
        _write(path, record)
    except Exception as exc:
        # Classification must not turn an expired decision into a replayable one.
        superseded = isinstance(exc, SupervisionSuperseded) or cancelled()
        busy = not superseded and _is_busy_control(exc)
        code = interruption_code() if interruption_code else ("cancelled" if cancelled() else None)
        code = code or ("timeout" if isinstance(exc, TimeoutError) else None)
        code = code or ("cancelled" if isinstance(exc, CancelledError) else None)
        code = code or ("observation_incomplete" if record.get("incomplete_requirements") else None)
        code = code or ("superseded" if superseded else None)
        code = code or ("busy" if busy else None)
        record["status"] = "superseded" if superseded else "issued"
        record["error"] = type(exc).__name__
        record["error_message"] = _short_error(exc)
        record["failure_stage"] = "commit"
        record.pop("stop_kind", None)
        record.pop("error_code", None)
        if code:
            record["error_code"] = code
        record["failure_reason"] = _failure_reason("commit", record, issued=True)
        if superseded:
            record["completed_at"] = time.time()
        try:
            _write(path, record)
        except OSError:
            LOG.exception("Manager supervision delivery checkpoint is unavailable")
        if busy:
            # A daemon running the mission this decision concerns is the
            # ordinary case, not a failure worth an event per attempt: the
            # issued event already stands, and the outcome is reported when
            # the decision is delivered or superseded at the next boundary.
            return record
    try:
        _emit(record, root, "applied" if record["status"] == "applied" else "failed")
    except OSError:
        LOG.exception("Manager supervision receipt event is unavailable")
    return record


def _active_tasks(root: Path) -> list[Any]:
    from ..life.memory import Backlog

    return list(Backlog(root / "backlog.jsonl").active())


def _attempt_key(root: Path, item_id: str) -> str:
    """Which run of a mission this is, so a STOP parks only the run it judged.

    A re-queue, an orphan retry or a retry after failure claims the item again
    (a new ``started_ts``) or advances its ``attempt``; either changes the key.
    """
    if not item_id:
        return ""
    task = next((task for task in _active_tasks(root) if task.id == item_id), None)
    if task is None:
        return ""
    return f"{int(getattr(task, 'attempt', 1) or 1)}:{getattr(task, 'started_ts', None) or ''}"


def _is_busy_control(exc: BaseException) -> bool:
    return isinstance(exc, SupervisionBusy) or str(exc) == "daemon control is busy"


def supervise(
    manager: Any, root: Path | str, event: dict[str, Any], *, backend: Any = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Judge new evidence without the pipeline lock, then safely apply its action."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "manager-supervision.lock").open("a+b") as lock:
        try:
            portalocker.lock(lock, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.exceptions.AlreadyLocked:
            return {"status": "busy"}
        try:
            observation = observe_project(root, event=event)
            latest = _latest_record(root)
            if latest.get("status") == "issued":
                return _deliver(root, latest.get("source_event", {}), latest, cancelled or (lambda: False))
            # A stalled turn or an Observer's remark is reported once and
            # changes no project file, so it is new evidence by itself.
            if (not observation.incomplete_requirements and latest.get("status") == "applied" and latest.get("evidence_revision") == observation.evidence_revision
                    and event.get("stall_reason") not in _REPORTED_ONCE
                    and observation.control_revision in {
                latest.get("control_revision"), latest.get("applied_control_revision"),
            }):
                return latest
            identity = uuid.uuid4().hex
            path = root / "manager-supervision" / f"{identity}.json"

            # No deadline: a 30 s cut lost 14 of 72 high-effort decisions in
            # s-a43d1018 (median 21 s), and a late decision is still checked
            # against newer evidence and control before it applies. Only
            # cancellation stops the call.
            def cancellation_code() -> str | None:
                if cancelled and cancelled():
                    return "cancelled"
                return None

            def cancelled_or_expired() -> bool:
                return cancellation_code() is not None

            def interruption_code() -> str | None:
                return cancellation_code() or ("superseded" if not observation.current() else None)

            def interrupted() -> bool:
                return interruption_code() is not None

            record: dict[str, Any] = {
                "version": 1, "id": identity,
                "evidence_revision": observation.evidence_revision,
                "control_revision": observation.control_revision,
                "trigger": {key: event[key] for key in ("type", "item_id", "event_id", "round_index", "consult_reason") if key in event},
                "source_event": (observation.facts["recent_events"][-1]
                                 if observation.facts["recent_events"] else {
                    key: event[key] for key in ("type", "item_id", "event_id", "agent_layer", "round_index") if key in event
                }) if event else {},
                "available_refs": observation.facts["evidence_refs"],
                "observation_limitations": observation.facts["limitations"],
                "incomplete_requirements": list(observation.incomplete_requirements),
                "evidence_refs": [], "cited_refs": [],
                "created_at": time.time(), "status": "evaluating",
            }
            _write(path, record)
            failure_stage = "provider"
            result = None
            try:
                if observation.incomplete_requirements:
                    failure_stage = "decision"
                    record["error_code"] = "observation_incomplete"
                    raise SupervisionDecisionError("observation_incomplete", "required project facts could not be fully observed")
                session: Any = _ManagerSession(backend, root) if backend is not None else manager._session
                from ._helpers import _manager_model, _manager_reasoning_effort

                def interrupt_reason() -> str | None:
                    code = interruption_code()
                    return {
                        "cancelled": "Manager supervision cancelled",
                        "superseded": "Manager supervision superseded",
                    }.get(code) if code else None

                options = RunnerOptions(
                    model=_manager_model(), reasoning_effort=_manager_reasoning_effort(),
                    skip_git_repo_check=True,
                    sandbox_mode="read-only", force_safe_mode=True, disable_tools=True,
                    working_dir=str(getattr(manager, "execution_workdir", root)),
                    external_interrupt_reason_provider=interrupt_reason,
                )
                actions = HandOffActions(
                    "supervise", check=partial(_check, root=root, event=event, observation=observation),
                )
                with run_interrupt_scope(interrupt_reason), role_actions(session, options, actions) as bound:
                    result = run_exec(session, prompt=_prompt(observation, str(event.get("consult_reason") or "")), options=bound, run_label="manager-supervision")
                record["call_id"] = getattr(result, "call_id", "") or ""
                record["backend_exit_code"] = int(getattr(result, "exit_code", 0) or 0)
                backend_failed, _ = _manager_backend_failure(result)
                if interrupted() or backend_failed:
                    raise RuntimeError("Manager supervision was interrupted or failed")
                failure_stage = "decision"
                if actions.handoff is None:
                    raise SupervisionDecisionError("handoff_missing", "Manager supervision ended without handing off")
                decision = actions.handoff.value
                available = {ref["path"]: ref for ref in observation.facts["evidence_refs"]}
                record["available_refs"] = list(available.values())
                record["cited_refs"] = [available[ref] for ref in decision["cited_refs"]]
                record["evidence_refs"] = record["cited_refs"]
                record["decision"] = decision
                failure_stage = "commit"
                record["status"] = "issued"
                record["issued_at"] = time.time()
                _write(path, record)
                try:
                    _emit(record, root, "issued")
                except Exception:
                    LOG.exception("Manager issued decision event is unavailable; the receipt remains authoritative")
                return _deliver(root, event, record, cancelled_or_expired, interruption_code=cancellation_code)
            except Exception as exc:
                durable = _read(path)
                if durable.get("status") == "issued":
                    return _deliver(root, event, durable, cancelled_or_expired, interruption_code=cancellation_code)
                record["status"] = "superseded" if interrupted() or isinstance(exc, SupervisionSuperseded) else "failed"
                record["error"] = type(exc).__name__
                record["error_message"] = _short_error(exc)
                record["failure_stage"] = failure_stage
                if isinstance(exc, SupervisionDecisionError):
                    record["error_code"] = exc.code
                elif failure_stage == "decision":
                    record["error_code"] = "decision_invalid"
                if failure_stage == "provider":
                    record.update(_provider_failure_metadata(result, exc))
                code = interruption_code()
                code = code or ("superseded" if isinstance(exc, SupervisionSuperseded) else None)
                if code:
                    record["error_code"] = code
                record["failure_reason"] = _failure_reason(failure_stage, record)
            record["completed_at"] = time.time()
            _write(path, record)
            _emit(record, root, "applied" if record["status"] == "applied" else "failed")
            return record
        finally:
            portalocker.unlock(lock)


def _worker(key: str, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            with _GUARD:
                current = _PENDING.pop(key, None)
            if current is None:
                return
            owner, state_root, source_event = current
            fork = owner.runner.fork
            backend = fork(event_callback=None) if "event_callback" in signature(fork).parameters else fork()
            configure_usage = getattr(backend, "set_usage_context", None)
            if callable(configure_usage):
                project = Path(state_root)
                configure_usage(
                    project_root=project, mission_id=source_event.get("item_id"),
                    global_root=project.parent.parent if project.parent.name == "projects" else None,
                )
            try:
                for attempt in range(8):
                    result = supervise(owner, state_root, source_event, backend=backend, cancelled=stop.is_set)
                    if result.get("status") not in {"issued", "busy"} or stop.wait(min(0.1 * 2 ** attempt, 1.0)):
                        break
            finally:
                close = getattr(backend, "close_acp_clients", None)
                if callable(close):
                    close()
    except Exception:
        LOG.exception("Manager supervision evidence is unavailable")
    finally:
        with _GUARD:
            _WORKERS.pop(key, None)
            if stop.is_set():
                _PENDING.pop(key, None)
            _ADMISSION.release()
            _dispatch_pending()


def _dispatch_pending() -> None:
    """Called under _GUARD; freed capacity admits retained project evidence."""
    if _CLOSED:
        return
    for key in list(_PENDING):
        if key in _WORKERS or key in _STOPPED_ROOTS:
            continue
        if not _ADMISSION.acquire(blocking=False):
            return
        stop = threading.Event()
        thread = threading.Thread(target=_worker, args=(key, stop), name="manager-supervision", daemon=True)
        _WORKERS[key] = (thread, stop)
        try:
            thread.start()
        except RuntimeError:
            _WORKERS.pop(key, None)
            _ADMISSION.release()
            LOG.exception("Manager supervision worker could not start")
            return


#: A ``stall_reason`` that is reported once and changes no project file, so it
#: is news by itself and newer evidence must not drop it: a stalled Engineer
#: turn, or the Observer telling the Manager that the work looks off.
_REPORTED_ONCE = frozenset({"turn_stalled", "observer"})

#: Boundaries that deliver a decision issued while the daemon was busy.
_DELIVERY_BOUNDARIES = frozenset({
    EventType.ROUND_REVIEW_COMPLETED, EventType.LIFE_MISSION_COMPLETED, EventType.LIFE_PLANNER_VERDICT,
})


def _review_consult_reason(root: Path, event: dict[str, Any]) -> str:
    """What a review puts to the Manager, or "" when nothing.

    A workspace that changed while the Reviewer judged, an Engineer's or a
    Reviewer's ``→ manager``, work that climbed to the Manager because it came
    back unchanged, or a check the Reviewer cannot make here while the
    operator cannot be asked, so accepting its risk falls to the Manager.
    A question goes to the operator, and how the work flows is the Observer's
    to report.
    """
    changed = event.get("changed_during_review")
    if changed:
        return (f"the workspace changed while the Reviewer judged ({', '.join(map(str, changed[:_CHANGED_PATHS_SHOWN]))}); "
                "its verdict stands as written: decide whether this review counts")
    handed = HANDED_TO_MANAGER.get(str(event.get("review_source") or ""))
    if (
        handed and event.get("status") == "blocked"
        and not event.get("operator_question") and not event.get("review_skipped")
    ):
        why = " ".join(str(event.get("reason") or "").split())[:200]
        return f"{handed}: {why}"
    if str(event.get("verification_obstacle") or "").strip() and _manager_holds_operator_authority(root):
        return "a check the Reviewer cannot make here, whose risk is yours to accept or not"
    return ""


def schedule_supervision(manager: Any, root: Path | str, event: dict[str, Any]) -> bool:
    """Admit what is put to the Manager, with bounded workers and project admission."""
    if not callable(getattr(getattr(manager, "runner", None), "fork", None)):
        return False
    event_type = event.get("type")
    project_root = Path(root)
    reason: str | None = ""
    if event_type in {EventType.LIFE_DAEMON_DEGRADED, EventType.LIFE_RUNTIME_INCIDENT_ESCALATED}:
        reason = None
    elif event_type == EventType.ROUND_REVIEW_COMPLETED and (
            event.get("status") in {"continue", "blocked"} or event.get("changed_during_review")):
        reason = _review_consult_reason(project_root, event)
    elif event_type == EventType.ROUND_STALL and event.get("stall_reason") == "turn_stalled":
        reason = (
            f"{event.get('text')}; to=team without a note keeps waiting on that turn, "
            "and a note stops it and restarts the Engineer with your instruction"
        )
    elif event_type == EventType.LIFE_OBSERVER_NOTE and event.get("to") == "manager":
        reason = f"the Observer, who watches how the team works, says: {event.get('text')}"
        event = {**event, "stall_reason": "observer"}
    if reason == "":
        # Nothing is put to the Manager; a boundary only delivers its issued decision.
        if event_type not in _DELIVERY_BOUNDARIES or _latest_record(project_root).get("status") != "issued":
            return False
        reason = None
    if reason:
        event = {**event, "consult_reason": reason}
    return _admit(manager, root, event)


def _admit(manager: Any, root: Path | str, event: dict[str, Any]) -> bool:
    key = str(Path(root).resolve())
    with _GUARD:
        if _CLOSED or key in _STOPPED_ROOTS:
            return False
        if key not in _PENDING and key not in _WORKERS and len(_PENDING) >= MAX_PENDING_PROJECTS:
            return False
        earlier = _PENDING.get(key, (None, None, {}))[2]
        _PENDING[key] = (manager, root, _carry_stall(earlier, event))
        _dispatch_pending()
    return True


def _carry_stall(earlier: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """``event``, still asking what the pending once-only report it replaces asked.

    A stalled turn and an Observer's remark are reported once, so newer
    evidence must not drop them: one look answers all.
    """
    event = dict(event)
    if earlier.get("stall_reason") in _REPORTED_ONCE:
        reasons = (earlier.get("consult_reason"), event.get("consult_reason"))
        event["consult_reason"] = "; ".join(dict.fromkeys(r for r in reasons if r))
        if event.get("stall_reason") not in _REPORTED_ONCE:
            event["stall_reason"] = earlier["stall_reason"]
    return event


def recover_issued_supervision(manager: Any, root: Path | str) -> bool:
    """Re-admit an unfinished durable delivery after service restart."""
    latest = _latest_record(Path(root))
    if latest.get("status") != "issued":
        return False
    event = latest.get("source_event", {})
    if not isinstance(event, dict) or not callable(getattr(getattr(manager, "runner", None), "fork", None)):
        return False
    # A durable issued decision is delivered whatever event it came from.
    return _admit(manager, root, event)


class SupervisionSink:
    """Observe real review/phase evidence where the mission emits it."""
    def __init__(self, sink: Any, supervisor: Any):
        self.inner = sink
        self.supervisor = supervisor

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def handle_event(self, event: dict[str, Any]) -> Any:
        result = self.inner.handle_event(event)
        if result is not False and self.supervisor.manager is not None:
            try:
                manager = self.supervisor._bound_manager()
                schedule_supervision(manager, manager.manager_session_root, event)
            except Exception:
                LOG.exception("could not schedule Manager evidence supervision")
        return result


def shutdown_supervision(root: Path | str | None = None, *, timeout: float = 1.0) -> int:
    """Cancel admission and bound shutdown even if an embedded backend ignores stop.

    Production backends receive the stop callback and terminate their provider.
    An uncooperative Python backend cannot be killed; its daemon worker cannot
    hold interpreter shutdown and remains fenced from applying late decisions.
    """
    global _CLOSED
    key = str(Path(root).resolve()) if root is not None else None
    with _GUARD:
        if key is None:
            _CLOSED = True
            _PENDING.clear()
        else:
            _STOPPED_ROOTS.add(key)
            _PENDING.pop(key, None)
        workers = [value for name, value in _WORKERS.items() if key is None or name == key]
        for thread, stop in workers:
            stop.set()
    deadline = time.monotonic() + max(0, timeout)
    for thread, _ in workers:
        if thread is not threading.current_thread():
            thread.join(timeout=max(0, deadline - time.monotonic()))
    return sum(thread.is_alive() for thread, _ in workers)


def start_supervision(root: Path | str | None = None) -> None:
    global _CLOSED
    with _GUARD:
        _CLOSED = False
        if root is not None:
            _STOPPED_ROOTS.discard(str(Path(root).resolve()))


__all__ = ["HANDED_TO_MANAGER", "supervise", "schedule_supervision", "recover_issued_supervision", "shutdown_supervision", "start_supervision", "SupervisionSink"]
