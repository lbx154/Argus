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
from inspect import signature
from pathlib import Path
from typing import Any, Callable

import portalocker

from ..core.event_catalog import EventType
from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec, run_interrupt_scope
from ..core.workspace_facts import fact_text
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
    _semantic,
    control_identity,
    observe_project,
)
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
        return bool(task and mission_id in waiting_ids and fact_text(_semantic(task)) == record.get("waiting_task_revisions", {}).get(mission_id))
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
    return f"Manager decision: {record['decision']['reason']}"


class SupervisionDecisionError(ValueError):
    """A Manager reply that could not become a decision, with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_DECISION_KEYS = (
    "ACTION", "REASON", "DIRECTIVE", "EVIDENCE_REFS", "CONSULTATION_ID", "ADVISOR_DISPOSITION",
    "ACCEPT_RISK", "RISK_CHECK", "RESIDUAL_RISK",
)
_BARE_KEY_LINE = re.compile(
    r"^[`*_]*(?P<key>" + "|".join(sorted(_DECISION_KEYS, key=len, reverse=True)) + r")[`*_]*\s+(?P<value>\S.*)$"
)

_COLON_KEY_LINE = re.compile(
    # Same decoration as role_reply's key pattern: a bullet, a quote marker or a
    # list number before the key.
    r"^(?:[-*+]\s*)?(?:[^\w`*]+\s*)?(?:\d+[.)]\s*)?[`*_]*(?:ARGUS_)?(?P<key>"
    + "|".join(sorted(_DECISION_KEYS, key=len, reverse=True))
    + r")[`*_]*\s*[:=]\s*(?P<value>.*)$",
    re.IGNORECASE,
)


def _bare_named_lines(text: str) -> dict[str, str]:
    """Read named lines when the reply wrote any of them without the colon.

    The key must be written in capitals at the start of its own line, exactly as
    the prompt names it, so ordinary prose ("Action items ...") is not read as a
    field. Like ``read_key_values`` this reads the final decision footer and the
    last line for a key wins, so a draft block followed by a final block yields
    the final answer; both the colon and colon-less forms are read in one pass
    so their order is kept. Returns nothing when every line carried a colon.
    """
    from ..core.role_reply import decision_footer_text

    found: dict[str, str] = {}
    saw_bare = False
    for raw in decision_footer_text(str(text or "")).splitlines():
        line = raw.strip()
        match = _COLON_KEY_LINE.match(line.strip("`").strip())
        if match is None:
            match = _BARE_KEY_LINE.match(line)
            saw_bare = saw_bare or match is not None
        if match is not None:
            found[match.group("key").upper()] = match.group("value").strip().strip("`").strip()
    return found if saw_bare else {}


_ACTIONS = ("continue", "steer", "wait", "stop")
_VERBS = "|".join(_ACTIONS)
# A verb counts only when a separator follows it (a colon, a dash or the end of
# the line) and what follows does not negate or hedge it: "STEER \u2014 rerun the
# fixture" is a decision, "Steer is not needed" and "wait and see" are not.
_LEADING_ACTION = re.compile(
    rf"^[`*_]*(?P<verb>{_VERBS})[`*_]*"
    r"(?:\s*$|\s*(?::|\u2014|\u2013|\s-\s)\s*(?P<rest>.*)$)",
    re.IGNORECASE | re.DOTALL,
)
_HEDGE = re.compile(
    r"^(?:not\b|no\b|never\b|unless\b|if\b|only if\b|maybe\b|perhaps\b|optional\b|"
    r"unnecessary\b|unneeded\b|hold off\b|do not\b|don't\b|"
    r"isn't\b|is not\b|would not\b|wouldn't\b|\?)",
    re.IGNORECASE,
)
_FIRST_VERB = re.compile(rf"^[`*_]*({_VERBS})\b", re.IGNORECASE)
# A rescue reader for replies that put every field on one line
# ("DECISION: STEER \u2014 ACTION: ... REASON: ... EVIDENCE_REFS: ...") or each
# key alone on a line with its value below it. Only capitalised keys count,
# exactly as the prompt writes them, so ordinary prose is never a field.
_RESCUE_KEYS = ("DECISION", *_DECISION_KEYS)
_RESCUE_INLINE = re.compile(
    r"(?<![A-Za-z0-9_])[`*_]*(?:ARGUS_)?(?P<key>"
    + "|".join(sorted(_RESCUE_KEYS, key=len, reverse=True))
    + r")[`*_]*\s*[:=]"
)
_RESCUE_BLOCK = re.compile(
    r"(?m)^[ \t>*#_`-]*(?P<key>"
    + "|".join(sorted(_RESCUE_KEYS, key=len, reverse=True))
    + r")[`*_]*[ \t]*$"
)


def _rescued_fields(text: str) -> dict[str, str]:
    """Split a reply into its capitalised named fields wherever they appear.

    Used only after the line reader found no complete decision, so no reply
    that parses today is read differently. Each field runs to the next key;
    the last occurrence of a key wins, like the line reader.
    """
    from ..core.role_reply import decision_footer_text

    source = decision_footer_text(str(text or ""))
    marks = sorted(
        [(match.start(), match.end(), match.group("key")) for match in _RESCUE_INLINE.finditer(source)]
        + [(match.start(), match.end(), match.group("key")) for match in _RESCUE_BLOCK.finditer(source)]
    )
    found: dict[str, str] = {}
    for index, (_start, end, key) in enumerate(marks):
        if index and marks[index - 1][1] > _start:
            continue
        stop = marks[index + 1][0] if index + 1 < len(marks) else len(source)
        value = " ".join(source[end:stop].split()).strip().strip("`").strip()
        # Trailing dashes separate fields; a trailing comma stays, so a verb
        # followed by one ("STEER, ...") is never read as a clean decision.
        value = value.rstrip("\u2014\u2013-|").strip()
        if value:
            found[key.lower()] = value
    return found


def _leading_verb(text: Any) -> str:
    match = _LEADING_ACTION.match(str(text or "").strip())
    if match is None or _HEDGE.match((match.group("rest") or "").strip()):
        return ""
    return match.group("verb").lower()


def _action_of(value: dict[str, Any]) -> str:
    """The chosen action: an exact ACTION, else the verb a DECISION or ACTION states.

    A DECISION whose verb differs from the verb the ACTION text starts with is
    a conflict, and no action is read from it.
    """
    stated = str(value.get("action") or "").strip().lower()
    decided = _leading_verb(value.get("decision"))
    if stated in _ACTIONS:
        return "conflicting" if decided and decided != stated else stated
    first = _FIRST_VERB.match(str(value.get("action") or "").strip())
    if decided and first and first.group(1).lower() != decided:
        return "conflicting"
    return decided or _leading_verb(value.get("action")) or stated


def _decision(text: str) -> dict[str, Any]:
    from ..core.role_reply import read_key_values

    try:
        value = json.loads(text)
    except ValueError:
        from ..core.role_handoff import read_baton_line

        fields = read_key_values(text, _DECISION_KEYS)
        fields.update(_bare_named_lines(text))
        value = {key.lower(): val for key, val in fields.items()}
        handoff = read_baton_line(text)
        if handoff is not None:
            value["handoff"], value["question"] = handoff.holder, handoff.question
    if not isinstance(value, dict):
        raise SupervisionDecisionError("decision_missing", "Manager supervision returned no decision")
    if isinstance(text, str) and "decision" not in value:
        # The line reader does not read DECISION; a DECISION that disagrees
        # with the stated ACTION makes the reply ambiguous.
        stated_decision = _rescued_fields(text).get("decision")
        if stated_decision:
            value = {**value, "decision": stated_decision}
    try:
        return _validated_decision(value)
    except SupervisionDecisionError:
        rescued = _rescued_fields(text) if isinstance(text, str) else {}
        if rescued:
            try:
                return _validated_decision(rescued)
            except SupervisionDecisionError:
                pass
        raise  # the first reading's failure names what was missing


# What a residual-risk field says when it accepts nothing. The Manager answers
# in prose, so "none", "(none)", "not applicable", "nothing accepted", "not
# accepted yet" and "none yet ..." must all read as no acceptance; only an
# explicit ACCEPT_RISK: yes with a named check and risk accepts anything.
_NO_ACCEPTANCE = re.compile(
    r"^(?:none|nil|null|n/?a|na|no|nothing|not|never|tbd|pending|unknown|undecided)\b",
    re.IGNORECASE,
)
_RISK_ID = re.compile(r"\brisk-[0-9a-f]{10}\b")
_RISK_VERB = re.compile(r"^[`*_\s]*(yes|no|revoke)\b", re.IGNORECASE)


def _risk_text(value: Any, limit: int = 1000) -> str:
    """The stated check or risk, or "" when the text accepts nothing."""
    text = " ".join(str(value or "").split())
    bare = text.strip(" .,;:`*_-()[]{}\"'").strip()
    if not bare or _NO_ACCEPTANCE.match(bare):
        return ""
    return text[:limit]


def _risk_decision(value: dict[str, Any]) -> dict[str, str]:
    """ACCEPT_RISK: yes (with RISK_CHECK and RESIDUAL_RISK), revoke <id>, or nothing.

    Anything else -- an absent or "no" ACCEPT_RISK, a check or risk that is
    empty or says none -- accepts nothing. A RESIDUAL_RISK without an explicit
    ACCEPT_RISK: yes is a remark, not an acceptance.
    """
    raw = " ".join(str(value.get("accept_risk") or "").split())
    verb = _RISK_VERB.match(raw)
    if verb is None:
        return {}
    if verb.group(1).lower() == "revoke":
        found = _RISK_ID.search(raw) or _RISK_ID.search(str(value.get("risk_check") or ""))
        return {"action": "revoke", "risk_id": found.group(0)} if found else {}
    if verb.group(1).lower() != "yes":
        return {}
    check, risk = _risk_text(value.get("risk_check"), 240), _risk_text(value.get("residual_risk"))
    return {"action": "accept", "check": check, "risk": risk} if check and risk else {}


def _validated_decision(value: dict[str, Any]) -> dict[str, Any]:
    action = _action_of(value)
    reason = str(value.get("reason") or "").strip()
    directive = str(value.get("directive") or "").strip()
    if action not in _ACTIONS or not reason or len(reason) > 4000:
        raise SupervisionDecisionError(
            "decision_incomplete",
            "Manager supervision decision is incomplete "
            f"(action={'unrecognized' if action else 'missing'}, reason={'present' if reason else 'missing'})",
        )
    if action == "steer" and (not directive or len(directive) > 4000):
        raise SupervisionDecisionError("directive_missing", "Steering requires a bounded team instruction")
    refs = value.get("evidence_refs", [])
    if isinstance(refs, str):
        refs = [ref.strip() for ref in refs.split(";") if ref.strip()]
    if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
        raise SupervisionDecisionError("evidence_uncited", "Manager must cite the evidence used for its decision")
    return {"action": action, "reason": reason, "directive": directive,
            # ``→ planner``: the directive is new planning, so the Planner is asked.
            "handoff": str(value.get("handoff") or ""),
            # ``→ operator: <question>``: the task handed here waits on their answer.
            "question": str(value.get("question") or ""),
            "cited_refs": list(dict.fromkeys(refs)),
            "consultation_id": str(value.get("consultation_id") or "")[:128],
            "advisor_disposition": str(value.get("advisor_disposition") or "")[:128],
            # Accepting (or revoking) the residual risk of one check impossible
            # here; see _risk_decision for what counts.
            "risk_decision": _risk_decision(value)}


def _manager_holds_operator_authority(root: Path | str | None = None) -> bool:
    """The Manager decides in the operator's place whenever they cannot be asked.

    That is a run with no operator, and equally a run whose question policy
    forbids asking: the operator decides only while they can be asked, so a
    decision never has no decider. ``reviewer.tools`` reads the same rule.
    """
    from ..core.autonomy import operator_questions_can_be_asked

    return not operator_questions_can_be_asked(root)


def _prompt(observation: ManagerObservation, consult_reason: str = "") -> str:
    from ..core.model_visible_text import FIXTURE_EVIDENCE_RULE

    asked = f"You are consulted now because of: {consult_reason}.\n" if consult_reason else ""
    # Who decides in the operator's place depends on this project's question policy.
    holds = _manager_holds_operator_authority(getattr(observation, "root", None))
    return (
        "You are the persistent project Manager.\n"
        + asked
        + "Judge from the evidence below whether the team's course serves the operator's "
        "objective, and decide one of:\n"
        "- CONTINUE: the course is right;\n"
        "- STEER: give the team a concrete corrected instruction, read at its next boundary;\n"
        + ("" if holds else "- WAIT: pause automatic planning until the operator answers the pending question;\n")
        + "- STOP: park this mission; changing direction will not help.\n"
        + (
            "The operator cannot be asked, so a check the Reviewer showed impossible here "
            "is yours to leave unverified: when the Reviewer read the best evidence reachable "
            "here, accept that one check's residual risk with ACCEPT_RISK: yes. "
            + FIXTURE_EVIDENCE_RULE
            + " Revoke an acceptance that no longer holds with ACCEPT_RISK: revoke <risk id>.\n"
            if holds else ""
        )
        + "End your reply with these fields, each on its own line in the form KEY: value:\n"
        + f"ACTION: {'continue, steer or stop' if holds else 'continue, steer, wait or stop'} (the single word)\n"
        "REASON: the decisive observed condition and what should happen next\n"
        "EVIDENCE_REFS: semicolon-separated paths from evidence_refs\n"
        "DIRECTIVE: the corrected team instruction (only for STEER)\n"
        + (
            "ACCEPT_RISK: yes, no, or revoke <risk id>\n"
            "RISK_CHECK: the one check left unverified (only with yes)\n"
            "RESIDUAL_RISK: the risk that leaves (only with yes)\n"
            if holds else ""
        )
        + "Add a last line `→ planner` only when a STEER needs new planning (tasks to add, "
        "retire or replace); otherwise whoever holds the work reads it at their next boundary.\n"
        + ("" if holds else (
            "When work handed to you waits on a choice only the operator can make, make that "
            "last line `→ operator: <one question>` instead: the task then waits on their answer.\n"
        ))
        + "\n"
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
    from ..core.operator_decision import build_operator_decision
    from ..life.event_log import JsonlEventSink
    from ..life.memory import Backlog
    from ..life.supervisor.pending_notify import notify_pending_question

    item_id = str(record.get("trigger", {}).get("item_id") or "")
    backlog = Backlog(root / "backlog.jsonl")
    task = next((row for row in backlog.active() if row.id == item_id), None) if item_id else None
    if task is None:
        raise SupervisionSuperseded("the task the question is about has ended")
    if task.status == "running":
        raise SupervisionBusy("the task the question is about is still settling")
    if task.pending_question != question:
        card = build_operator_decision(
            item_id=task.id, title=task.title, reason=record["decision"]["reason"],
            question=question, project_id=root.name, previous_decision=task.operator_decision,
        )
        task = backlog.update(
            task.id, park_reason="operator_question", pending_question=question, operator_decision=card,
        ) or task
        notify_pending_question(root, task)
        JsonlEventSink(None, life_dir=root).append({
            "type": EventType.LIFE_OPERATOR_QUESTION_PENDING, "item_id": task.id,
            "title": task.title, "question": question, "agent_layer": "manager",
        })
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
            if decision["action"] == "wait" and not decision.get("question") and not any(
                item.get("pending_question") for item in observation.facts["tasks"]
            ):
                raise SupervisionSuperseded("operator question no longer waits")
            effects = record.setdefault("effects", {"supervision_id": record["id"]})
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
                        # An Observer's remark never moves the baton, so a steer
                        # it prompted is read by the team, never handed to the Planner.
                        for_planner=decision.get("handoff") == "planner"
                        and record["trigger"].get("type") != EventType.LIFE_OBSERVER_NOTE,
                    )
                effects.update(directive_revision=directive.revision, directive_delivered=True, inbox_queued=False)
                _apply_risk_decision(root, record, event, observation, effects)
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
                with run_interrupt_scope(interrupt_reason):
                    result = run_exec(session, prompt=_prompt(observation, str(event.get("consult_reason") or "")), options=options, run_label="manager-supervision")
                record["call_id"] = getattr(result, "call_id", "") or ""
                record["backend_exit_code"] = int(getattr(result, "exit_code", 0) or 0)
                backend_failed, _ = _manager_backend_failure(result)
                if interrupted() or backend_failed:
                    raise RuntimeError("Manager supervision was interrupted or failed")
                failure_stage = "decision"
                decision = _decision(extract_answer(result))
                if (decision["action"] == "wait" or decision["question"]) and _manager_holds_operator_authority(root):
                    raise SupervisionDecisionError(
                        "wait_unavailable", "the operator cannot be asked, so nothing waits on them",
                    )
                if decision["action"] == "wait":
                    waiting = [task for task in observation.facts["tasks"] if task.get("pending_question")]
                    record["waiting_task_ids"] = [task["id"] for task in waiting]
                    record["waiting_task_revisions"] = {task["id"]: fact_text(_semantic(task)) for task in waiting}
                    record["waiting_questions"] = {task["id"]: task["pending_question"] for task in waiting}
                available = {ref["path"]: ref for ref in observation.facts["evidence_refs"]}
                cited = decision["cited_refs"]
                if any(ref not in available for ref in cited):
                    outside = sum(ref not in available for ref in cited)
                    raise SupervisionDecisionError(
                        "evidence_outside_snapshot",
                        f"Manager cited {outside} of {len(cited)} evidence references outside the observed project snapshot",
                    )
                record["available_refs"] = list(available.values())
                record["cited_refs"] = [available[ref] for ref in cited]
                record["evidence_refs"] = record["cited_refs"]
                consultation_id = decision.get("consultation_id")
                if consultation_id:
                    from ..advisor.receipts import read_receipt

                    advice = read_receipt(root, consultation_id)
                    if not advice or advice.get("status") != "completed":
                        raise SupervisionDecisionError("advisor_unavailable", "Manager referenced unavailable advisor evidence")
                    if decision.get("advisor_disposition") not in {"adopt", "reject"}:
                        raise SupervisionDecisionError("advisor_disposition_missing", "Manager must explain whether it adopted the advisor result")
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
        return (f"the workspace changed while the Reviewer judged ({', '.join(map(str, changed[:20]))}); "
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
            f"{event.get('text')}; CONTINUE keeps waiting on that turn, STEER stops it "
            "and restarts the Engineer with your directive"
        )
    elif event_type == EventType.LIFE_OBSERVER_NOTE and event.get("to") == "manager":
        said = event.get("evidence") or event.get("text")
        reason = f"the Observer, who watches how the team works, says: {said}"
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


__all__ = ["HANDED_TO_MANAGER", "supervise", "schedule_supervision", "recover_issued_supervision", "waiting_for_evidence", "mission_wait_reason", "shutdown_supervision", "start_supervision", "SupervisionSink"]
