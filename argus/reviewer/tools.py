"""The review form: the Reviewer's judgment arrives as its ``hand_off`` call (``core.handoff``)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Mapping

from ..core.handoff import HandOffActions
from ..core.models import ReviewDecision
from ..core.operator_decision import normalize_agent_options
from ..core.research_contract import RESULT_FIELD_CHOICES, normalize_research_result
from ..core.venue_review import ACCEPTED_RECOMMENDATIONS, RECOMMENDATIONS


@dataclass(frozen=True)
class ReviewGrounding:
    """What a Reviewer's quotes and acceptances are checked against.

    ``task_text`` is the task as the Reviewer was given it (objective, scope,
    packet context). ``roots`` are the directories a quoted file may come from,
    and a file there counts only if it is still as the Planner recorded it when
    the task packet named it (``packet_refs``: path and ``file_facts``), or as
    it was when the first mission on this objective began (``baseline``:
    resolved path to ``file_facts``); with no baseline, no unnamed file counts. So
    nothing an Engineer wrote or edited, in this mission or an earlier one on
    the objective, is a source: a statement that a check is impossible must
    come from the task, its packet or the environment.

    ``accepted_risks`` are the Manager's acceptances in force for this item and
    ``operator_decisions`` the operator's resolved decision cards for it. An
    acceptance is one of those records, never text the Reviewer was shown.
    """

    task_text: str = ""
    roots: tuple[str, ...] = ()
    operator_available: bool = True
    baseline: Mapping[str, list[int]] | None = None
    packet_refs: tuple[tuple[str, list[int]], ...] = ()
    accepted_risks: tuple[dict[str, Any], ...] = ()
    operator_decisions: tuple[dict[str, Any], ...] = ()


_QUOTE_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "`": " ", "*": " "})
_MIN_QUOTE_CHARS = 20
_MAX_SOURCE_BYTES = 2_000_000


def _folded(text: str) -> str:
    return " ".join(str(text or "").translate(_QUOTE_FOLD).casefold().split())


def _packet_facts(candidate: Path, grounding: ReviewGrounding, base: Path) -> list[int]:
    """The ``file_facts`` the Planner recorded when the task packet named ``candidate``, or []."""
    for ref, facts in grounding.packet_refs:
        try:
            named = Path(ref).expanduser()
            named = (named if named.is_absolute() else base / named).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if named == candidate and facts:
            return facts
    return []


def _not_original(candidate: Path, source: str, grounding: ReviewGrounding, base: Path) -> str:
    """Why ``candidate`` is not the task's or environment's own file, or "" when it is."""
    from ..core.file_facts import file_facts

    try:
        current = file_facts(candidate)
    except OSError:
        return f"{source} cannot be read"
    recorded = _packet_facts(candidate, grounding, base)
    if recorded:
        return "" if current == recorded else (
            f"{source} has changed since the task packet named it, so the words there "
            "are this objective's own work, not the packet's"
        )
    if grounding.baseline is not None and grounding.baseline.get(str(candidate)) == current:
        return ""
    return (
        f"{source} is not as it was when work on this objective began and the task "
        "packet does not name it, so it is this objective's own work, not the environment"
    )


def _quote_found(quote: str, source: str, grounding: ReviewGrounding) -> str:
    """Why ``quote`` is not grounded in ``source``, or "" when it is."""
    needle = _folded(quote).strip(" .\"'")
    if len(needle) < _MIN_QUOTE_CHARS:
        return f"quote at least {_MIN_QUOTE_CHARS} characters of the statement, verbatim"
    source = str(source or "").strip()
    if source.lower() in {"", "task", "packet", "task text"}:
        return "" if needle in _folded(grounding.task_text) else (
            "that statement is not in the task text or packet you were given"
        )
    for root in grounding.roots:
        try:
            base = Path(root).resolve()
            candidate = (base / source).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if not candidate.is_relative_to(base) or not candidate.is_file():
            continue
        problem = _not_original(candidate, source, grounding, base)
        if problem:
            return problem
        try:
            with candidate.open("rb") as handle:
                text = handle.read(_MAX_SOURCE_BYTES).decode("utf-8", "replace")
        except OSError:
            continue
        if needle in _folded(text):
            return ""
        return f"that statement is not in {source}"
    return f"{source} is not a readable file in this workspace"


#: A research vertical's assessment of the result, beside the review form's own fields.
_RESEARCH_RESULT: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{key: {"type": "string", "enum": list(values)} for key, values in RESULT_FIELD_CHOICES},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
    "required": [key for key, _ in RESULT_FIELD_CHOICES[:4]],
    "additionalProperties": False,
}


def review_actions(
    *, venue: str = "", venue_required: bool = False, grounding: ReviewGrounding | None = None,
) -> HandOffActions:
    """The Reviewer's form for one call; a venue review also states its recommendation."""
    from ..core.autonomy import operator_questions_can_be_asked

    grounding = grounding or ReviewGrounding(operator_available=operator_questions_can_be_asked())
    fields: dict[str, Any] = {"research_result": _RESEARCH_RESULT}
    if venue_required:
        fields["recommendation"] = {
            "type": "string", "enum": list(RECOMMENDATIONS),
            "description": (
                f"Your actual current recommendation for {venue}, not a future "
                "aspiration. Required with to=done and to=engineer."
            ),
        }
    return HandOffActions(
        "review", fields=fields, operator_available=grounding.operator_available,
        check=partial(_review, venue=venue, venue_required=venue_required, grounding=grounding),
    )


def _review(
    payload: dict[str, Any], *, venue: str, venue_required: bool, grounding: ReviewGrounding,
) -> ReviewDecision:
    """The verdict a review call records, or a refusal the Reviewer can act on."""
    to = payload["to"]
    if to != "done" and "residual_risk" in payload:
        raise ValueError("residual_risk records an accepted risk: it goes with to=done.")
    if to == "done" and ("unverifiable" in payload or "impossible_because" in payload):
        raise ValueError("A check left unverified on to=done is a residual_risk.")
    if to != "operator" and "accept_risk" in payload:
        raise ValueError("accept_risk belongs to a question: hand the work to=operator.")
    if venue_required and to in {"done", "engineer"} and "recommendation" not in payload:
        raise ValueError(f"State your current recommendation for {venue}.")
    research = normalize_research_result(payload.get("research_result"))
    if "research_result" in payload and research is None:
        raise ValueError("The research assessment is incomplete.")
    _check_grounding(payload, grounding)
    options = payload.get("ask", {}).get("options", ())
    if "accept_risk" in payload:
        from ..core.residual_risk import acceptance_options

        options = acceptance_options(payload["accept_risk"]["check"], payload["accept_risk"]["risk"])
    decision = ReviewDecision(
        status=to, reason=payload["summary"], next_action=payload["note"],
        operator_question=payload.get("ask", {}).get("question", ""),
        operator_options=normalize_agent_options(options),
        research_result=research,
        session_signal=payload.get("session_signal", {}),
        assumptions=list(payload["assumptions"]),
    )
    judged = payload.get("learning")
    if to == "done" and judged:
        decision.learning = judged["verdict"]
        decision.learning_reason = judged["reason"][:500]
    decision.verification_obstacle = payload.get("unverifiable", "").strip()
    basis = payload.get("impossible_because")
    if basis:
        decision.verification_obstacle_basis = " ".join(basis["quote"].split())[:1000]
        decision.verification_obstacle_basis_source = " ".join(basis["source"].split())[:300]
    risk = payload.get("residual_risk")
    if risk:
        decision.residual_risk, decision.residual_risk_detail = _residual_risk_record(
            risk, _acceptance_record(risk, grounding),
        )
    if "recommendation" in payload:
        recommendation = payload["recommendation"]
        decision.venue_review = {
            "venue": venue, "recommendation": recommendation,
            "acceptance_clear": recommendation in ACCEPTED_RECOMMENDATIONS,
            "rationale": payload["note"], "blocking_issues": [],
            "revision_required": to != "done",
        }
    return decision


def _check_grounding(payload: dict[str, Any], grounding: ReviewGrounding) -> None:
    """Refuse an ungrounded "impossible here" or an unaccepted residual risk.

    The refusal goes back to the Reviewer as the tool's error, so it can
    correct the call: quote the statement, ask the right party, or revise.
    """
    basis = payload.get("impossible_because")
    if basis:
        if not payload.get("unverifiable", "").strip():
            raise ValueError("impossible_because supports unverifiable; name the check there too.")
        problem = _quote_found(basis["quote"], basis["source"], grounding)
        if problem:
            raise ValueError(
                f"impossible_because: {problem}. A check is impossible here only when "
                "the task, its packet or the environment says so; otherwise it is "
                "missing, so ask the Engineer for it."
            )
    risk = payload.get("residual_risk")
    if not risk:
        return
    problem = _quote_found(risk["impossible_because"]["quote"], risk["impossible_because"]["source"], grounding)
    if problem:
        raise ValueError(
            f"residual_risk.impossible_because: {problem}. Without that statement the "
            "check is missing, not impossible: return it to the Engineer instead of accepting."
        )
    _acceptance_record(risk, grounding)


def _acceptance_record(risk: dict[str, Any], grounding: ReviewGrounding) -> str:
    """The recorded acceptance of ``risk``'s check, as "<id>: <how>"; raises without one."""
    from ..core.residual_risk import ACCEPT_OPTION_LABEL, operator_accepted, same_check

    if risk["accepted_by"] == "manager":
        if grounding.operator_available:
            raise ValueError(
                "The operator can be asked, so accepting this risk is theirs to decide: "
                "ask them with to=operator and accept_risk."
            )
        wanted = str(risk["acceptance"]).strip().strip("[]")
        row = next((
            row for row in grounding.accepted_risks
            if row.get("id") == wanted and row.get("accepted_by") == "manager"
        ), None)
        if row is None:
            raise ValueError(
                "residual_risk.acceptance must be a risk id listed under Accepted "
                "residual risk for this task; without one, the Manager has not accepted it."
            )
        if not same_check(row.get("check"), risk["check"]):
            raise ValueError(
                f"{wanted} accepts the check \"{row.get('check')}\", not this one; name that "
                "check exactly, or return the work to the Engineer."
            )
        return f"{wanted}: accepted by the Manager"
    if not grounding.operator_available:
        raise ValueError(
            "The operator cannot be asked in this run; only the Manager can accept "
            "this risk. Name it in unverifiable and hand the work on instead."
        )
    card = next((
        card for card in reversed(grounding.operator_decisions) if operator_accepted(card, risk["check"])
    ), None)
    if card is None:
        raise ValueError(
            "The operator has not accepted this check: only their choice of "
            f"\"{ACCEPT_OPTION_LABEL}\" on a question raised with accept_risk naming "
            "this exact check accepts it; their other words do not. Ask them, or "
            "return the work to the Engineer."
        )
    return f"{card.get('id') or 'decision'}: the operator chose \"{ACCEPT_OPTION_LABEL}\""


def _residual_risk_record(risk: dict[str, Any], acceptance: str) -> tuple[str, dict[str, Any]]:
    from ..core.residual_risk import sanitize_text

    check, rest = sanitize_text(risk["check"], 240), sanitize_text(risk["risk"])
    who = "the operator" if risk["accepted_by"] == "operator" else "the Manager"
    detail = {
        "check": check, "risk": rest, "evidence": sanitize_text(risk["evidence"]),
        "basis": sanitize_text(risk["impossible_because"]["quote"], 400),
        "basis_source": sanitize_text(risk["impossible_because"]["source"], 200),
        # The host's record of the acceptance, never the Reviewer's wording.
        "accepted_by": risk["accepted_by"], "acceptance": sanitize_text(acceptance, 400),
    }
    return f"{check}: {rest} (accepted by {who})", detail
