"""Evidence-triggered Manager judgments with durable, applied control receipts."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import CancelledError
from inspect import signature
from pathlib import Path
from typing import Any, Callable

import portalocker

from ..core.event_catalog import EventType
from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec, run_interrupt_scope
from ..daemon.state import _fsync_directory, compare_and_swap_continuous_config
from ._helpers import _manager_backend_failure
from ._session_ops import (
    _ManagerSession,
    clear_manager_pipeline_yield,
    request_manager_pipeline_yield,
)
from .observation import ManagerObservation, _digest, _semantic, control_identity, observe_project
from .session_context import manager_session_yield_reason
from .stage_decider import extract_answer
from .supervision_errors import _failure_reason, _provider_failure_metadata

LOG = logging.getLogger(__name__)
_ADMISSION = threading.BoundedSemaphore(2)
_WORKERS: dict[str, tuple[threading.Thread, threading.Event]] = {}
_PENDING: dict[str, tuple[Any, Path | str, dict[str, Any]]] = {}
_GUARD = threading.Lock()
_CLOSED = False
_STOPPED_ROOTS: set[str] = set()
MAX_PENDING_PROJECTS = 64


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
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _latest_record(root: Path) -> dict[str, Any]:
    latest = _read(root / "manager-supervision" / "latest.json")
    identity = str(latest.get("id") or "")
    if len(identity) == 64 and all(char in "0123456789abcdef" for char in identity):
        durable = _read(root / "manager-supervision" / f"{identity}.json")
        if durable.get("id") == identity:
            return durable
    return latest


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
        "supervision_id": record["id"], "evidence_revision": record["evidence_revision"],
        "control_revision": record["control_revision"],
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
    JsonlEventSink(None, life_dir=root).append(payload)


def waiting_for_evidence(root: Path | str | None, mission_id: str | None = None) -> bool:
    """An applied WAIT defers repeated planning only while its evidence is current."""
    if root is None:
        return False
    root = Path(root)
    record = _latest_record(root)
    if record.get("status") != "applied" or record.get("decision", {}).get("action") != "wait":
        return False
    observation = observe_project(root, event=record.get("source_event", {}))
    if observation.control_revision != record.get("applied_control_revision"):
        return False
    questions = {item["id"]: item for item in observation.facts["tasks"] if item.get("pending_question")}
    waiting_ids = set(record.get("waiting_task_ids", []))
    if mission_id is not None:
        task = questions.get(mission_id)
        return bool(task and mission_id in waiting_ids and _digest(_semantic(task)) == record.get("waiting_task_revisions", {}).get(mission_id))
    return bool(
        observation.evidence_revision == record.get("evidence_revision")
        and waiting_ids.intersection(questions)
    )


def mission_wait_reason(root: Path | str | None, mission_id: str | None) -> str:
    """A safe role boundary may pause only this still-waiting running mission."""
    if root is None or not mission_id or not waiting_for_evidence(root, mission_id):
        return ""
    from ..life.memory import Backlog

    task = next((item for item in Backlog(Path(root) / "backlog.jsonl").active() if item.id == mission_id), None)
    if task is None or task.status != "running" or not task.pending_question:
        return ""
    return f"Manager is waiting for the requested operator facts: {task.pending_question}"


def _decision(text: str) -> dict[str, Any]:
    from ..core.role_reply import read_key_values

    try:
        value = json.loads(text)
    except ValueError:
        fields = read_key_values(text, ("ACTION", "REASON", "DIRECTIVE", "EVIDENCE_REFS", "CONSULTATION_ID", "ADVISOR_DISPOSITION"))
        value = {key.lower(): val for key, val in fields.items()}
    if not isinstance(value, dict):
        raise ValueError("Manager supervision returned no decision")
    action = str(value.get("action") or "").strip().lower()
    reason = str(value.get("reason") or "").strip()
    directive = str(value.get("directive") or "").strip()
    if action not in {"continue", "steer", "wait"} or not reason or len(reason) > 4000:
        raise ValueError("Manager supervision decision is incomplete")
    if action == "steer" and (not directive or len(directive) > 4000):
        raise ValueError("Steering requires a bounded team instruction")
    refs = value.get("evidence_refs", [])
    if isinstance(refs, str):
        refs = [ref.strip() for ref in refs.split(";") if ref.strip()]
    if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
        raise ValueError("Manager must cite the evidence used for its decision")
    return {"action": action, "reason": reason, "directive": directive,
            "cited_refs": list(dict.fromkeys(refs)),
            "consultation_id": str(value.get("consultation_id") or "")[:128],
            "advisor_disposition": str(value.get("advisor_disposition") or "")[:128]}


def _prompt(observation: ManagerObservation) -> str:
    return (
        "You are the persistent project Manager, supervising the team's progress toward the "
        "operator's actual objective. Assess the concrete evidence below. A successful tool "
        "call or an unchanged review is not progress. Preserve the user's requirements and "
        "acceptance criteria. Do not rerun reviews or planning while awaiting the same missing "
        "human facts. Separate unreviewed Engineer claims from verified results.\n"
        "Choose CONTINUE if the current course is justified; STEER to give a concrete corrected "
        "instruction through the persistent Manager direction read at the team's next boundary; WAIT only when a persisted operator "
        "question prevents further work. WAIT pauses automatic planning and preserves the "
        "task and question. Do not use WAIT for ordinary implementation failures; steer a fix. "
        "You do not change the objective, acceptance standard, or pipeline stage here.\n"
        "Return named lines ACTION, REASON, EVIDENCE_REFS (semicolon-separated paths from evidence_refs), "
        "and DIRECTIVE (only for STEER). REASON must name "
        "the decisive observed condition and what should happen next.\n\n"
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
                raise RuntimeError("daemon control is busy")
            observation = observe_project(root, event=event)
            if cancelled() or not (
                observation.control_revision == record["control_revision"]
                or _owns_reserved_control(root, record)
            ):
                raise SupervisionSuperseded("newer project control")
            if observation.evidence_revision != record["evidence_revision"]:
                raise SupervisionSuperseded("newer project evidence")
            if decision["action"] == "wait" and not any(
                item.get("pending_question") for item in observation.facts["tasks"]
            ):
                raise SupervisionSuperseded("operator question no longer waits")
            effects = record.setdefault("effects", {"supervision_id": record["id"]})
            if decision["action"] == "continue":
                effects["effect"] = "current course retained"
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
                    )
                effects.update(directive_revision=directive.revision, directive_delivered=True, inbox_queued=False)
            effects["automatic_planning_paused"] = decision["action"] == "wait"
            if decision["action"] == "wait":
                effects["waiting_task_ids"] = record["waiting_task_ids"]
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
        for key in ("failure_reason", "failure_stage", "error", "error_type", "error_code", "stop_kind"):
            record.pop(key, None)
        _write(path, record)
    except Exception as exc:
        # Classification must not turn an expired decision into a replayable one.
        superseded = isinstance(exc, SupervisionSuperseded) or cancelled()
        code = interruption_code() if interruption_code else ("cancelled" if cancelled() else None)
        code = code or ("timeout" if isinstance(exc, TimeoutError) else None)
        code = code or ("cancelled" if isinstance(exc, CancelledError) else None)
        code = code or ("superseded" if superseded else None)
        record["status"] = "superseded" if superseded else "issued"
        record["error"] = type(exc).__name__
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
    try:
        _emit(record, root, "applied" if record["status"] == "applied" else "failed")
    except OSError:
        LOG.exception("Manager supervision receipt event is unavailable")
    return record


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
            if (latest.get("status") == "applied" and latest.get("evidence_revision") == observation.evidence_revision
                    and observation.control_revision in {
                latest.get("control_revision"), latest.get("applied_control_revision"),
            }):
                return latest
            identity = hashlib.sha256(
                (observation.evidence_revision + ":" + observation.control_revision).encode()
            ).hexdigest()
            path = root / "manager-supervision" / f"{identity}.json"
            previous = _read(path)
            if previous.get("status") == "applied" and observation.control_revision in {
                previous.get("control_revision"), previous.get("applied_control_revision"),
            }:
                return previous
            deadline = time.monotonic() + 30

            def cancellation_code() -> str | None:
                if cancelled and cancelled():
                    return "cancelled"
                if time.monotonic() >= deadline:
                    return "timeout"
                if manager_session_yield_reason(root):
                    return "superseded"
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
                "trigger": {key: event[key] for key in ("type", "item_id", "event_id") if key in event},
                "source_event": observation.facts["recent_events"][-1] if event else {},
                "available_refs": observation.facts["evidence_refs"],
                "evidence_refs": [], "cited_refs": [],
                "created_at": time.time(), "status": "evaluating",
            }
            _write(path, record)
            failure_stage = "provider"
            result = None
            try:
                session: Any = _ManagerSession(backend, root) if backend is not None else manager._session
                from ._helpers import _manager_model, _manager_reasoning_effort

                def interrupt_reason() -> str | None:
                    code = interruption_code()
                    return {
                        "timeout": "Manager supervision timed out",
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
                with run_interrupt_scope(interrupt_reason):
                    result = run_exec(session, prompt=_prompt(observation), options=options, run_label="manager-supervision")
                record["call_id"] = getattr(result, "call_id", "") or ""
                record["backend_exit_code"] = int(getattr(result, "exit_code", 0) or 0)
                backend_failed, _ = _manager_backend_failure(result)
                if interrupted() or backend_failed:
                    raise RuntimeError("Manager supervision was interrupted or failed")
                failure_stage = "decision"
                decision = _decision(extract_answer(result))
                if decision["action"] == "wait":
                    waiting = [task for task in observation.facts["tasks"] if task.get("pending_question")]
                    record["waiting_task_ids"] = [task["id"] for task in waiting]
                    record["waiting_task_revisions"] = {task["id"]: _digest(_semantic(task)) for task in waiting}
                    record["waiting_questions"] = {task["id"]: task["pending_question"] for task in waiting}
                available = {ref["path"]: ref for ref in observation.facts["evidence_refs"]}
                cited = decision["cited_refs"]
                if any(ref not in available for ref in cited):
                    raise ValueError("Manager cited evidence outside the observed project snapshot")
                record["available_refs"] = list(available.values())
                record["cited_refs"] = [available[ref] for ref in cited]
                record["evidence_refs"] = record["cited_refs"]
                consultation_id = decision.get("consultation_id")
                if consultation_id:
                    from ..advisor.receipts import read_receipt

                    advice = read_receipt(root, consultation_id)
                    if not advice or advice.get("status") != "completed":
                        raise ValueError("Manager referenced unavailable advisor evidence")
                    if decision.get("advisor_disposition") not in {"adopt", "reject"}:
                        raise ValueError("Manager must explain whether it adopted the advisor result")
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
                record["failure_stage"] = failure_stage
                if failure_stage == "provider":
                    record.update(_provider_failure_metadata(result, exc))
                code = interruption_code()
                code = code or ("superseded" if isinstance(exc, SupervisionSuperseded) else None)
                if code:
                    record["error_code"] = code
                    if code == "timeout" and failure_stage == "provider":
                        record["stop_kind"] = "transient_error"
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


def schedule_supervision(manager: Any, root: Path | str, event: dict[str, Any]) -> bool:
    """Coalesce new evidence with bounded workers and bounded project admission."""
    event_type = event.get("type")
    relevant = event_type in {EventType.LIFE_MISSION_COMPLETED, EventType.LIFE_PLANNER_VERDICT,
                              EventType.LIFE_DAEMON_DEGRADED}
    relevant |= event_type == EventType.ROUND_REVIEW_COMPLETED and event.get("status") in {"continue", "blocked"}
    relevant |= event_type == EventType.LIFE_PHASE_STARTED and event.get("agent_layer") == "engineer" and int(event.get("round_index") or 0) > 1
    if not relevant or not callable(getattr(getattr(manager, "runner", None), "fork", None)):
        return False
    key = str(Path(root).resolve())
    with _GUARD:
        if _CLOSED or key in _STOPPED_ROOTS:
            return False
        if key not in _PENDING and key not in _WORKERS and len(_PENDING) >= MAX_PENDING_PROJECTS:
            return False
        _PENDING[key] = (manager, root, dict(event))
        _dispatch_pending()
    return True


def recover_issued_supervision(manager: Any, root: Path | str) -> bool:
    """Re-admit an unfinished durable delivery after service restart."""
    latest = _latest_record(Path(root))
    if latest.get("status") != "issued":
        return False
    event = latest.get("source_event", {})
    return schedule_supervision(manager, root, event) if isinstance(event, dict) else False


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


__all__ = ["supervise", "schedule_supervision", "recover_issued_supervision", "waiting_for_evidence", "mission_wait_reason", "shutdown_supervision", "start_supervision", "SupervisionSink"]
