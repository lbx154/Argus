"""Evidence-triggered Manager judgments with durable, applied control receipts."""
from __future__ import annotations

import hashlib
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
from ..daemon.state import _fsync_directory, compare_and_swap_continuous_config
from ._helpers import _manager_backend_failure
from ._session_ops import (
    _ManagerSession,
    clear_manager_pipeline_yield,
    request_manager_pipeline_yield,
)
from .observation import (
    ManagerObservation,
    _digest,
    _read_object,
    _semantic,
    control_identity,
    observe_project,
)
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
    if len(identity) == 64 and all(char in "0123456789abcdef" for char in identity):
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


_ACTIONS = ("continue", "steer", "wait")
# A verb counts only when a separator follows it (a colon, a dash or the end of
# the line) and what follows does not negate or hedge it: "STEER \u2014 rerun the
# fixture" is a decision, "Steer is not needed" and "wait and see" are not.
_LEADING_ACTION = re.compile(
    r"^[`*_]*(?P<verb>continue|steer|wait)[`*_]*"
    r"(?:\s*$|\s*(?::|\u2014|\u2013|\s-\s)\s*(?P<rest>.*)$)",
    re.IGNORECASE | re.DOTALL,
)
_HEDGE = re.compile(
    r"^(?:not\b|no\b|never\b|unless\b|if\b|only if\b|maybe\b|perhaps\b|optional\b|"
    r"unnecessary\b|unneeded\b|hold off\b|do not\b|don't\b|"
    r"isn't\b|is not\b|would not\b|wouldn't\b|\?)",
    re.IGNORECASE,
)
_FIRST_VERB = re.compile(r"^[`*_]*(continue|steer|wait)\b", re.IGNORECASE)
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
        fields = read_key_values(text, _DECISION_KEYS)
        fields.update(_bare_named_lines(text))
        value = {key.lower(): val for key, val in fields.items()}
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
    if action not in {"continue", "steer", "wait"} or not reason or len(reason) > 4000:
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
            "cited_refs": list(dict.fromkeys(refs)),
            "consultation_id": str(value.get("consultation_id") or "")[:128],
            "advisor_disposition": str(value.get("advisor_disposition") or "")[:128],
            # Accepting (or revoking) the residual risk of one check impossible
            # here; see _risk_decision for what counts.
            "risk_decision": _risk_decision(value)}


def _manager_accepts_risk() -> bool:
    """Only in a run with no operator does the Manager accept a residual risk."""
    from ..core.autonomy import operator_available

    return not operator_available()


def _risk_authority_rule() -> str:
    from ..core.model_visible_text import FIXTURE_EVIDENCE_RULE

    if not _manager_accepts_risk():
        return (
            "Leaving such a check unverified changes the acceptance standard, so it is "
            "the operator's decision, not yours: the Reviewer asks them. Meanwhile steer "
            "toward the best evidence reachable here. "
            + FIXTURE_EVIDENCE_RULE + "\n"
        )
    return (
        "No operator is available, so for such a check you decide whether to leave it "
        "unverified: when the Reviewer has read the best evidence reachable here, "
        "accept that one check's residual risk with ACCEPT_RISK: yes; when that "
        "evidence is still missing, steer the Engineer to build it. "
        + FIXTURE_EVIDENCE_RULE
        + " An Engineer claim is never that evidence, and an acceptance covers only "
        "the named check: the Reviewer still judges completion. Revoke an acceptance "
        "that no longer holds with ACCEPT_RISK: revoke <risk id>.\n"
    )


def _standard_rule() -> str:
    if _manager_accepts_risk():
        return (
            "Apart from accepting or revoking one grounded check's residual risk as "
            "above, you do not change the objective, acceptance standard, or pipeline "
            "stage here. "
        )
    return "You do not change the objective, acceptance standard, or pipeline stage here. "


def _risk_fields() -> str:
    if not _manager_accepts_risk():
        return ""
    return (
        "ACCEPT_RISK: yes, no, or revoke <risk id>\n"
        "RISK_CHECK: the one check left unverified (only with yes)\n"
        "RESIDUAL_RISK: the risk that leaves (only with yes)\n"
    )


def _prompt(observation: ManagerObservation, consult_reason: str = "") -> str:
    asked = f"You are consulted now because of: {consult_reason}.\n" if consult_reason else ""
    return (
        "You are the persistent project Manager, supervising the team's progress toward the "
        "operator's actual objective. Assess the concrete evidence below. A successful tool "
        "call or an unchanged review is not progress. Preserve the user's requirements and "
        "acceptance criteria. Do not rerun reviews or planning while awaiting the same missing "
        "human facts. Separate unreviewed Engineer claims from verified results. When the "
        "Engineer and Reviewer report conflicting values for the same checkable fact, that is "
        "a dispute: neither side is verified by its role. Decide it by whose evidence carries "
        "its own labels and a reproducible command; if neither does, steer both to produce "
        "that evidence rather than adopting either side's number. A review's "
        "verification_obstacle names a decisive check that cannot happen here; more "
        "rounds of the same request cannot settle it. If the fact is only hidden "
        "(e.g. a masked display), steer toward a rerunnable check whose result both "
        "can see. A check is impossible here only when the Reviewer quoted the task, "
        "packet or environment saying what it needs exists only at grading or deploy "
        "time (verification_obstacle_basis, quoted from verification_obstacle_basis_source); "
        "without that quote it is missing, so "
        "steer toward it. "
        + _risk_authority_rule()
        + "Choose CONTINUE if the current course is justified; STEER to give a concrete corrected "
        "instruction through the persistent Manager direction read at the team's next boundary; WAIT only when a persisted operator "
        "question prevents further work. WAIT pauses automatic planning and preserves the "
        "task and question. Do not use WAIT for ordinary implementation failures; steer a fix. "
        + _standard_rule()
        + "Scheduling belongs to Argus and its operator: never direct a role to pause, "
        "stop or reschedule work by editing Argus's own state.\n"
        "End your reply with these fields, each on its own line in the form KEY: value:\n"
        "ACTION: continue, steer or wait (the single word)\n"
        "REASON: the decisive observed condition and what should happen next\n"
        "EVIDENCE_REFS: semicolon-separated paths from evidence_refs\n"
        "DIRECTIVE: the corrected team instruction (only for STEER)\n"
        + _risk_fields()
        + "\n"
        + asked
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
    if not _manager_accepts_risk():
        effects["residual_risk_refused"] = "an operator is available; accepting a residual risk is theirs"
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
            if observation.evidence_revision != record["evidence_revision"]:
                raise SupervisionSuperseded("newer project evidence")
            if decision["action"] == "wait" and not any(
                item.get("pending_question") for item in observation.facts["tasks"]
            ):
                raise SupervisionSuperseded("operator question no longer waits")
            effects = record.setdefault("effects", {"supervision_id": record["id"]})
            if decision["action"] == "continue":
                effects["effect"] = "current course retained"
                _apply_risk_decision(root, record, event, observation, effects)
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
        _record_look(root, record.get("trigger") or {})
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


def _looks_path(root: Path) -> Path:
    return root / "manager-supervision" / "looks.json"


#: Missions remembered in the look index; the least recently judged go first.
MAX_REMEMBERED_LOOKS = 500


def _attempt_key(root: Path, item_id: str) -> str:
    """Which run of a mission this is: rounds restart at 1 on every run.

    A re-queue, an orphan retry or a retry after failure claims the item again
    (a new ``started_ts``) or advances its ``attempt``; either changes the key.
    """
    if not item_id:
        return ""
    task = next((task for task in _active_tasks(root) if task.id == item_id), None)
    if task is None:
        return ""
    return f"{int(getattr(task, 'attempt', 1) or 1)}:{getattr(task, 'started_ts', None) or ''}"


def _read_looks(root: Path) -> dict[str, dict[str, Any]]:
    looks: dict[str, dict[str, Any]] = {}
    for item_id, value in _read(_looks_path(root)).items():
        if isinstance(value, int):  # an index written before attempts were kept
            value = {"round": value, "attempt": "", "ts": 0.0}
        if isinstance(value, dict) and isinstance(value.get("round"), int):
            looks[str(item_id)] = value
    return looks


def _record_look(root: Path, trigger: dict[str, Any]) -> None:
    """Remember the reviewed round of a mission run the Manager effectively judged."""
    item_id = str(trigger.get("item_id") or "")
    round_index = _round(trigger.get("round_index"))
    if not item_id or round_index <= 0:
        return
    path = _looks_path(root)
    looks = _read_looks(root)
    looks[item_id] = {
        "round": round_index, "attempt": str(trigger.get("attempt_key") or ""), "ts": time.time(),
    }
    if len(looks) > MAX_REMEMBERED_LOOKS:
        recent = sorted(looks, key=lambda key: float(looks[key].get("ts") or 0.0))[-MAX_REMEMBERED_LOOKS:]
        looks = {key: looks[key] for key in recent}
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(looks), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        LOG.warning("Manager look index is unavailable", exc_info=True)
    finally:
        temporary.unlink(missing_ok=True)


def _last_look(root: Path, item_id: str, *, attempt: str = "", round_index: int = 0) -> int:
    """The last judged round of this run of the mission, or 0 when none.

    A look from another run never counts: its attempt key differs, or, when the
    key is unknown, the current round is below the recorded one, which only a
    restarted run can produce.
    """
    look = _read_looks(root).get(item_id) if item_id else None
    if not look:
        return 0
    if str(look.get("attempt") or "") != attempt or (round_index and round_index < look["round"]):
        return 0
    return int(look["round"])


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
            if (not observation.incomplete_requirements and latest.get("status") == "applied" and latest.get("evidence_revision") == observation.evidence_revision
                    and observation.control_revision in {
                latest.get("control_revision"), latest.get("applied_control_revision"),
            }):
                return latest
            identity = hashlib.sha256(
                (observation.evidence_revision + ":" + observation.control_revision).encode()
            ).hexdigest()
            path = root / "manager-supervision" / f"{identity}.json"
            previous = _read(path)
            if not observation.incomplete_requirements and previous.get("status") == "applied" and observation.control_revision in {
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
                "trigger": {key: event[key] for key in ("type", "item_id", "event_id", "round_index", "consult_reason", "attempt_key") if key in event},
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
                    result = run_exec(session, prompt=_prompt(observation, str(event.get("consult_reason") or "")), options=options, run_label="manager-supervision")
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


#: Within a mission the Manager judges at least once every this many reviewed
#: rounds, whatever the Reviewer said, so a confidently wrong Reviewer is caught.
REVIEW_CHECKPOINT_ROUNDS = 3
#: Consecutive no-progress verdicts after which the Manager is asked even when
#: the Reviewer did not ask; half the default stall limit, so it can still act.
STALL_BACKSTOP_STREAK = 2
#: Reviewer authority impacts that put the decision beyond the Engineer.
_ESCALATED_AUTHORITY = frozenset({"manager_contract", "operator"})


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    return True if text == "true" else False if text == "false" else None


def _round(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _active_tasks(root: Path) -> list[Any]:
    from ..life.memory import Backlog

    return list(Backlog(root / "backlog.jsonl").active())


def _safety_net(root: Path) -> str:
    """Pending things a Reviewer cannot see from its round; never a judgment."""
    if _latest_record(root).get("status") == "issued":
        return "issued decision awaiting delivery"
    if any(task.pending_question for task in _active_tasks(root)):
        return "operator question pending"
    return ""


def _reviewer_signal(event: dict[str, Any]) -> str:
    """A structured Reviewer signal that already means the Manager is wanted."""
    plan_signal = str(event.get("plan_signal") or "").strip().lower()
    if plan_signal and plan_signal != "continue":
        return f"reviewer plan signal: {plan_signal}"
    if str(event.get("plan_challenge") or "").strip():
        return "reviewer challenges the plan"
    if str(event.get("authority_impact") or "").strip().lower() in _ESCALATED_AUTHORITY:
        return "decision beyond the engineer's authority"
    if _flag(event.get("checkpoint_recommended")):
        return "reviewer recommends a checkpoint"
    signal = event.get("session_signal")
    if isinstance(signal, dict) and str(signal.get("kind") or "").strip():
        return "reviewer session signal"
    if str(event.get("verification_obstacle") or "").strip():
        return "evidence the reviewer cannot observe"
    return ""


def _attention_reason(event: dict[str, Any]) -> str:
    """Whether the Reviewer asked for the Manager; absent means it did.

    A verdict the host wrote or rewrote (any source other than the Reviewer)
    carries no Reviewer judgment, so it reads as absent.
    """
    source = str(event.get("review_source") or "reviewer").strip().lower()
    attention = str(event.get("manager_attention") or "").strip().lower() if source == "reviewer" else ""
    if attention == "not_needed":
        return _reviewer_signal(event)
    if attention == "needed":
        why = " ".join(str(event.get("manager_attention_reason") or "").split())[:200]
        return f"reviewer asks for the Manager: {why}" if why else "reviewer asks for the Manager"
    return "reviewer did not say whether the Manager is needed"


def _review_consult_reason(root: Path, event: dict[str, Any]) -> str:
    """Why a mid-mission review needs the Manager's judgment, or "" when it does not.

    The Reviewer, which already judges the round, says in ``manager_attention``
    whether the course needs a Manager look, and its other structured signals
    (a plan challenge, an authority question, a checkpoint, a session problem,
    unobservable evidence) count as asking. Only safety nets it cannot see or
    that are no judgment run regardless: a blocked review or operator question,
    a missing or unusable verdict, and a pending Manager decision. A run of
    no-progress verdicts reaches the Manager through ``round.stall``, and the
    Manager judges at least every ``REVIEW_CHECKPOINT_ROUNDS`` reviewed rounds.
    """
    if event.get("status") == "blocked":
        return "blocked review"
    if str(event.get("operator_question") or "").strip():
        return "operator question"
    if _flag(event.get("review_skipped")) or _flag(event.get("backend_unavailable")):
        return "review verdict unavailable"
    reason = _safety_net(root) or _attention_reason(event)
    if reason:
        return reason
    # A sparse check that does not depend on the Reviewer being right: within a
    # mission the Manager judges at least every few reviewed rounds. Only a
    # decision that took effect counts as having looked.
    round_index = _round(event.get("round_index"))
    item_id = str(event.get("item_id") or "")
    last = _last_look(root, item_id, attempt=_attempt_key(root, item_id), round_index=round_index)
    if round_index - last >= REVIEW_CHECKPOINT_ROUNDS:
        return "periodic checkpoint"
    return ""


def _latest_review(root: Path, item_id: str = "") -> dict[str, Any]:
    from ..life.memory import _read_jsonl_tail_history

    for row in reversed(_read_jsonl_tail_history(root / "events.jsonl", 400)):
        if row.get("type") == EventType.ROUND_REVIEW_COMPLETED and (
            not item_id or row.get("item_id") == item_id
        ):
            return row
    return {}


def _settled_consult_reason(root: Path, event: dict[str, Any]) -> str | None:
    """Whether a reviewed success needs the Manager.

    ``None`` means the event is not a reviewed success. A bounded run that is
    ending has no course to steer, and a check started now races orderly
    daemon shutdown. In continuous mode the final review's
    ``manager_attention`` decides, as during the mission; without it the
    Manager looks. Planner verdicts are always supervised.
    """
    from ..daemon.state import read_continuous_state

    if event.get("type") != EventType.LIFE_MISSION_COMPLETED or not (
        event.get("success") is True and event.get("status") == "done"
    ):
        return None
    net = _safety_net(root)
    if net or _active_tasks(root):
        return net or "work remains after the mission"
    if not read_continuous_state(root).enabled:
        return ""
    review = _latest_review(root, str(event.get("item_id") or ""))
    return _attention_reason(review) if review else "no final review to consult"


def schedule_supervision(manager: Any, root: Path | str, event: dict[str, Any]) -> bool:
    """Coalesce new evidence with bounded workers and bounded project admission."""
    event_type = event.get("type")
    relevant = event_type in {EventType.LIFE_MISSION_COMPLETED, EventType.LIFE_PLANNER_VERDICT,
                              EventType.LIFE_DAEMON_DEGRADED,
                              EventType.LIFE_RUNTIME_INCIDENT_ESCALATED}
    relevant |= event_type == EventType.ROUND_REVIEW_COMPLETED and event.get("status") in {"continue", "blocked"}
    relevant |= event_type == EventType.ROUND_STALL and _round(event.get("semantic_stall_streak")) >= STALL_BACKSTOP_STREAK
    if not relevant or not callable(getattr(getattr(manager, "runner", None), "fork", None)):
        return False
    project_root = Path(root)
    reason: str | None = None
    if event_type == EventType.ROUND_REVIEW_COMPLETED:
        reason = _review_consult_reason(project_root, event)
    elif event_type == EventType.ROUND_STALL:
        reason = f"{_round(event.get('semantic_stall_streak'))} rounds without forward progress"
    elif event_type == EventType.LIFE_MISSION_COMPLETED:
        reason = _settled_consult_reason(project_root, event)
    if reason == "":
        LOG.debug("Manager supervision not requested for %s", event_type)
        return False
    if reason:
        event = {**event, "consult_reason": reason}
    if event_type == EventType.ROUND_REVIEW_COMPLETED:
        # Lets an applied decision record which run of the mission it judged.
        event = {**event, "attempt_key": _attempt_key(project_root, str(event.get("item_id") or ""))}
    return _admit(manager, root, event)


def _admit(manager: Any, root: Path | str, event: dict[str, Any]) -> bool:
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


__all__ = ["supervise", "schedule_supervision", "recover_issued_supervision", "waiting_for_evidence", "mission_wait_reason", "shutdown_supervision", "start_supervision", "SupervisionSink"]
