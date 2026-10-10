"""Call-bound review actions. Review prose is evidence, never a wire protocol."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator, Mapping

from jsonschema import ValidationError, validate

from ..core.model_visible_text import FIXTURE_EVIDENCE_RULE
from ..core.models import ReviewDecision, ReviewStatus, RunnerOptions
from ..core.operator_decision import normalize_agent_options, normalize_option_id
from ..core.research_contract import RESULT_FIELD_CHOICES, normalize_research_result
from ..core.role_tool_bridge import CallBoundBridge, bridge_request
from ..core.venue_review import ACCEPTED_RECOMMENDATIONS, RECOMMENDATIONS
from .validation import (
    CANCEL_TOOL,
    COMMAND_TOOL,
    RESULT_TOOL,
    ReviewValidation,
    configured_read_dirs,
    configured_validation,
)

PREFIX = "ARGUS_PLUGIN_REVIEW"
SERVER = "argus_review_actions"
_ACTIONS: dict[str, tuple[ReviewStatus, str]] = {
    "approve_review": (
        "done",
        "The current task is complete; no required repair remains. Only after you "
        "checked each requirement and symptom the task (this increment) states, and "
        "each acceptance criterion, against the deliverable: tests the Engineer wrote "
        "show the code matches its reading of the task, not that the reading is "
        "right, and a stated symptom called intended or out of scope needs support "
        "in the task text itself.",
    ),
    "revise_review": ("continue", "Return concrete in-scope repairs to Engineer."),
    "defer_review": ("continue", "Defer judgment until already-running work or missing external evidence returns. This is not failure or acceptance."),
    "request_review_decision": ("blocked", "Ask an actual operator-owned question; ordinary technical repairs use revise_review."),
    "replan_review": ("replan_requested", "Challenge the current plan or scope with evidence and a proposed alternative."),
}


#: Verdicts whose Manager supervision follows the Reviewer's attention
#: judgment; a blocked or replanning verdict always reaches the Manager.
_ATTENTION_ACTIONS = frozenset({"approve_review", "revise_review", "defer_review"})


def _judgment_fields() -> dict[str, dict[str, Any]]:
    """Optional routing judgments; a malformed one is ignored, never fatal."""
    def judgment(description: str, verdicts: list[str]) -> dict[str, Any]:
        return {
            "type": "object", "description": description,
            "properties": {"verdict": {"enum": verdicts}, "reason": {"type": "string"}},
            "required": ["verdict", "reason"], "additionalProperties": False,
        }

    return {
        "manager_attention": judgment(
            "Does the course need the Manager now: stuck, looping or drifting, plan "
            "doubt, agreement on something unverified, evidence only the operator "
            "can supply (credentials, hardware, human judgment), or scope or "
            "authority? A check you lack is not one: run it if you can, else ask "
            "the Engineer for it. When unsure, needed.",
            ["needed", "not_needed"],
        ),
        "learning": judgment(
            "Is anything from this task worth keeping for later work: a failure, a "
            "correction, a surprise, a new technique, a reusable pattern? When "
            "unsure, worth_reflecting.",
            ["worth_reflecting", "nothing_new"],
        ),
    }


@dataclass(frozen=True)
class ReviewGrounding:
    """What a Reviewer's quotes and acceptances are checked against.

    ``task_text`` is the task as the Reviewer was given it (objective, scope,
    packet context). ``roots`` are the directories a quoted file may come from,
    and a file there counts only if its sha256 now is the one the Planner
    recorded when the task packet named it (``packet_refs``: path and hash),
    or the one it had when the first mission on this objective began
    (``baseline``: resolved path to hash); with no baseline, no unnamed file
    counts. Content, not timestamps, which can be set. So nothing an Engineer
    wrote or edited, in this mission or an earlier one on the objective, is a
    source: a statement that a check is impossible must come from the task,
    its packet or the environment.

    ``input_roots`` are the task's own input directories outside the workdir
    (also among ``roots``); the Reviewer may read them, never write them.

    ``accepted_risks`` are the Manager's acceptances in force for this item and
    ``operator_decisions`` the operator's resolved decision cards for it. An
    acceptance is one of those records, never text the Reviewer was shown.
    """

    task_text: str = ""
    roots: tuple[str, ...] = ()
    input_roots: tuple[str, ...] = ()
    operator_available: bool = True
    baseline: Mapping[str, str] | None = None
    packet_refs: tuple[tuple[str, str], ...] = ()
    accepted_risks: tuple[dict[str, Any], ...] = ()
    operator_decisions: tuple[dict[str, Any], ...] = ()


_QUOTE_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "`": " ", "*": " "})
_MIN_QUOTE_CHARS = 20
_MAX_SOURCE_BYTES = 2_000_000


def _folded(text: str) -> str:
    return " ".join(str(text or "").translate(_QUOTE_FOLD).casefold().split())


def _packet_hash(candidate: Path, grounding: ReviewGrounding, base: Path) -> str:
    """The sha256 the Planner recorded when the task packet named ``candidate``, or ""."""
    for ref, digest in grounding.packet_refs:
        try:
            named = Path(ref).expanduser()
            named = (named if named.is_absolute() else base / named).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if named == candidate and digest:
            return digest
    return ""


def _not_original(candidate: Path, source: str, grounding: ReviewGrounding, base: Path) -> str:
    """Why ``candidate`` is not the task's or environment's own file, or "" when it is."""
    from ..core.grounding_baseline import file_sha256

    try:
        current = file_sha256(candidate)
    except OSError:
        return f"{source} cannot be read"
    recorded = _packet_hash(candidate, grounding, base)
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


def _basis_field() -> dict[str, Any]:
    return {
        "type": "object",
        "description": (
            "Required when you call a check impossible here because what it needs "
            "exists only at grading or deploy time: the task, packet or environment "
            "statement that says so, quoted verbatim, and its source ('task', or a "
            "file in the workspace or the task's own input directories that the packet "
            "names or that is unchanged since work on this objective began; never one "
            "written or edited since). The host checks the quote. Without such a statement "
            "the check is missing, not impossible."
        ),
        "properties": {
            "quote": {"type": "string", "minLength": _MIN_QUOTE_CHARS},
            "source": {"type": "string", "minLength": 1},
        },
        "required": ["quote", "source"],
        "additionalProperties": False,
    }


class ReviewActions:
    def __init__(
        self, *, venue: str = "", venue_required: bool = False,
        validation: ReviewValidation | None = None,
        grounding: ReviewGrounding | None = None,
    ) -> None:
        from ..core.autonomy import operator_available

        self.venue = venue
        self.venue_required = venue_required
        self.validation = validation
        self.grounding = grounding or ReviewGrounding(operator_available=operator_available())
        self.decision: ReviewDecision | None = None
        self.tools = self._tools()

    def _tools(self) -> list[dict[str, Any]]:
        research = {
            "type": "object",
            "properties": {
                **{key: {"type": "string", "enum": list(values)} for key, values in RESULT_FIELD_CHOICES},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": [key for key, _ in RESULT_FIELD_CHOICES[:4]],
            "additionalProperties": False,
        }
        tools = []
        judgments = _judgment_fields()
        for name, (_, description) in _ACTIONS.items():
            fields: dict[str, Any] = {
                "review": {"type": "string", "minLength": 1, "description": "Your complete natural-language review, including evidence and any next steps. No template or status footer."},
                "forward_progress": {"type": "boolean", "description": "Whether this round moved toward the operator's goal."},
                "research_result": research,
                "session_signal": {
                    "type": "object",
                    "description": "An observed role-session quality problem, not an ordinary task repair.",
                    "properties": {
                        "kind": {"enum": ["repeated_contradiction", "reviewer_confusion", "quality_degradation"]},
                        "target": {"enum": ["planner", "engineer", "reviewer"]},
                        "detail": {"type": "string"},
                    },
                    "required": ["kind", "target", "detail"],
                    "additionalProperties": False,
                },
                "frontier_report": {
                    "type": "object",
                    "description": "Optional evidence of how the remaining work changed this round.",
                    "properties": {
                        "change": {"enum": [
                            "artifact_improved", "risk_reduced", "uncertainty_reduced",
                            "information_gain", "bounded_regression", "recovered",
                            "unchanged_failure", "expanding_regression", "unexplained_regression",
                        ]},
                        **{key: {"type": "string"} for key in (
                            "summary", "hypothesis", "uncertainty", "next_decision_point",
                        )},
                        **{key: {"type": "array", "items": {"type": "string"}} for key in (
                            "resolved_obligations", "new_obligations", "regressed_obligations",
                            "remaining_work", "proxy_changes", "artifacts", "evidence",
                        )},
                        "regression": {
                            "type": "object",
                            "properties": {key: {"type": "string"} for key in (
                                "cause", "scope", "budget", "recovery_test", "exit_trigger",
                            )},
                            "additionalProperties": False,
                        },
                    },
                    "required": ["change"],
                    "additionalProperties": False,
                },
            }
            # Only where the host uses them: attention on verdicts that do not
            # already reach the Manager, learning on the accepting verdict.
            if name in _ATTENTION_ACTIONS:
                fields["manager_attention"] = judgments["manager_attention"]
            if name == "approve_review":
                fields["learning"] = judgments["learning"]
            # A replanning Reviewer can be stopped by the same obstacle; one
            # task replanned four times over a token only its grader receives.
            if _ACTIONS[name][0] in {"continue", "replan_requested"}:
                fields["unverifiable"] = {
                    "type": "string",
                    "description": (
                        "Only when a decisive check cannot happen in this environment, "
                        "so repeating the finding cannot settle it: the fact is hidden "
                        "from every view you have (e.g. masked or redacted display), or "
                        "the task, packet or environment says the check needs a resource "
                        "that exists only at grading or deploy time (quote it in "
                        "impossible_because). Name the check, the best evidence you can "
                        "reach instead (a rerunnable check, or a test fixture), and the "
                        "risk left. " + FIXTURE_EVIDENCE_RULE + " " + (
                            "Leaving it unverified is the operator's decision: ask them "
                            "with request_review_decision (operator_need "
                            "scope_or_authority, accept_risk)."
                            if self.grounding.operator_available else
                            "No operator is available, so the Manager sees this and "
                            "decides whether to accept that risk."
                        )
                    ),
                }
                fields["impossible_because"] = _basis_field()
            if name == "approve_review":
                fields["residual_risk"] = {
                    "type": "object",
                    "description": (
                        "Only when one decisive check stays unverified because it is "
                        "impossible here and leaving it so was accepted: by the operator "
                        "choosing to accept it on the request_review_decision you raised "
                        "with accept_risk, or, in a run with no operator, by the Manager "
                        "(cite the risk id listed under Accepted residual risk). Name the "
                        "check exactly as accepted. Without that acceptance, do not "
                        "approve. The evidence you judged "
                        "it from must be something you read yourself. "
                        + FIXTURE_EVIDENCE_RULE
                        + " The Engineer's word alone is never that evidence."
                    ),
                    "properties": {
                        "check": {"type": "string", "minLength": 3},
                        "evidence": {"type": "string", "minLength": 3},
                        "risk": {"type": "string", "minLength": 3},
                        "impossible_because": _basis_field(),
                        "accepted_by": {"enum": ["operator", "manager"]},
                        "acceptance": {
                            "type": "string", "minLength": 3,
                            "description": (
                                "The Manager's risk id, or the operator's decision id; the host "
                                "checks the recorded choice, not this text."
                            ),
                        },
                    },
                    "required": [
                        "check", "evidence", "risk", "impossible_because", "accepted_by", "acceptance",
                    ],
                    "additionalProperties": False,
                }
            required = ["review"]
            if self.venue_required and name in {"approve_review", "revise_review"}:
                fields["recommendation"] = {
                    "type": "string", "enum": list(RECOMMENDATIONS),
                    "description": f"Your actual current recommendation for {self.venue}, not a future aspiration.",
                }
                required.append("recommendation")
            if name == "request_review_decision":
                fields["question"] = {"type": "string", "minLength": 1}
                fields["options"] = {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"}, "label": {"type": "string"},
                        "description": {"type": "string"}, "requires_note": {"type": "boolean"},
                    },
                    "required": ["id", "label", "description"],
                    "additionalProperties": False,
                }}
                fields["operator_need"] = {
                    "type": "string",
                    "enum": [
                        "credentials", "spending", "irreversible_or_external",
                        "scope_or_authority", "none",
                    ],
                    "description": (
                        "Why only the operator can decide: credentials (real secrets the "
                        "work needs), spending, irreversible_or_external, or "
                        "scope_or_authority; none if the team can decide."
                    ),
                }
                fields["accept_risk"] = {
                    "type": "object",
                    "description": (
                        "When the question is whether to leave one check impossible here "
                        "unverified: name the check and the risk. The host adds the choice to "
                        "accept that risk or keep the check; only choosing it accepts."
                    ),
                    "properties": {
                        "check": {"type": "string", "minLength": 3},
                        "risk": {"type": "string", "minLength": 3},
                    },
                    "required": ["check", "risk"],
                    "additionalProperties": False,
                }
                required.append("question")
            if name == "replan_review":
                fields["alternative"] = {"type": "string"}
                fields["authority_impact"] = {"type": "string", "enum": ["technical", "manager_contract", "operator"]}
                required.append("authority_impact")
            tools.append({
                "name": name, "description": description,
                "inputSchema": {"type": "object", "properties": fields, "required": required, "additionalProperties": False},
            })
        if self.validation is not None:
            tools.extend(self.validation.tools())
        return tools

    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "tools":
            return {"tools": self.tools}
        tool = next((tool for tool in self.tools if tool["name"] == action), None)
        if tool is None:
            raise ValueError(f"Unknown review action: {action}")
        properties = tool["inputSchema"].get("properties", {})
        payload = dict(payload)
        for key in ("manager_attention", "learning"):
            if key not in payload:
                continue
            try:
                validate(payload[key], properties[key])
            except (KeyError, ValidationError):
                # Absent rather than fatal: the verdict itself stands, and an
                # absent judgment means the Manager looks and reflection runs.
                payload.pop(key)
        try:
            validate(payload, tool["inputSchema"])
        except ValidationError as exc:
            raise ValueError(exc.message) from exc
        if action == COMMAND_TOOL and self.validation is not None:
            return self.validation.run(payload)
        if action in {RESULT_TOOL, CANCEL_TOOL} and self.validation is not None:
            return self.validation.result(payload["command_id"], cancel=action == CANCEL_TOOL)
        review = payload["review"]
        if not review.strip():
            raise ValueError("The review must explain the judgment.")
        question = payload.get("question", "")
        if action == "request_review_decision" and not question.strip():
            raise ValueError("State the operator-owned question.")
        research = normalize_research_result(payload.get("research_result"))
        if "research_result" in payload and research is None:
            raise ValueError("The research assessment is incomplete.")
        self._check_grounding(payload)
        status = _ACTIONS[action][0]
        options = [
            option for option in payload.get("options", [])
            # Only the host offers an acceptance choice, bound to its check. Judged
            # on the id the card will carry, so "_accept-risk-…" or "Accept Risk …"
            # cannot pass here and become the host's id after normalisation.
            if not normalize_option_id(option.get("id")).startswith("accept-risk")
        ]
        if isinstance(payload.get("accept_risk"), dict):
            from ..core.residual_risk import acceptance_options

            options = [
                *acceptance_options(payload["accept_risk"]["check"], payload["accept_risk"]["risk"]),
                *options,
            ]
        decision = ReviewDecision(
            status=status, reason=review,
            next_action="" if status == "done" else review,
            operator_question=question,
            operator_options=normalize_agent_options(options),
            research_result=research,
            planner_report={"plan_signal": "continue"},
            session_signal=payload.get("session_signal", {}),
            frontier_report=payload.get("frontier_report", {}),
        )
        if "forward_progress" in payload:
            decision.planner_report["forward_progress"] = payload["forward_progress"]
        if action == "request_review_decision" and payload.get("operator_need"):
            decision.planner_report["operator_need"] = payload["operator_need"]
        for key in ("manager_attention", "learning"):
            judged = payload.get(key)
            if isinstance(judged, dict):
                decision.planner_report[key] = judged["verdict"]
                decision.planner_report[f"{key}_reason"] = str(judged.get("reason") or "")[:500]
        decision.verification_obstacle = str(payload.get("unverifiable") or "").strip()
        basis = payload.get("impossible_because")
        if isinstance(basis, dict):
            decision.verification_obstacle_basis = " ".join(str(basis["quote"]).split())[:1000]
            decision.verification_obstacle_basis_source = " ".join(str(basis["source"]).split())[:300]
        risk = payload.get("residual_risk")
        if isinstance(risk, dict):
            decision.residual_risk, decision.residual_risk_detail = _residual_risk_record(
                risk, self._acceptance_record(risk),
            )
        if action == "replan_review":
            decision.planner_report.update(
                plan_signal="reconsider", challenge=review,
                alternative=payload.get("alternative", ""),
                authority_impact=payload["authority_impact"],
            )
        if "recommendation" in payload:
            recommendation = payload["recommendation"]
            decision.venue_review = {
                "venue": self.venue, "recommendation": recommendation,
                "acceptance_clear": recommendation in ACCEPTED_RECOMMENDATIONS,
                "rationale": review, "blocking_issues": [],
                "revision_required": action != "approve_review",
            }
        # A native action belongs to this call. Prose, tool output printed in a
        # message, and saved project files cannot manufacture this assignment.
        self.decision = decision
        return {"recorded": action}

    def _check_grounding(self, payload: dict[str, Any]) -> None:
        """Refuse an ungrounded "impossible here" or an unaccepted residual risk.

        The refusal goes back to the Reviewer as the tool's error, so it can
        correct the call: quote the statement, ask the right party, or revise.
        """
        grounding = self.grounding
        basis = payload.get("impossible_because")
        if isinstance(basis, dict):
            if not str(payload.get("unverifiable") or "").strip():
                raise ValueError("impossible_because supports unverifiable; name the check there too.")
            problem = _quote_found(basis["quote"], basis["source"], grounding)
            if problem:
                raise ValueError(
                    f"impossible_because: {problem}. A check is impossible here only when "
                    "the task, its packet or the environment says so; otherwise it is "
                    "missing, so ask the Engineer for it."
                )
        risk = payload.get("residual_risk")
        if not isinstance(risk, dict):
            return
        problem = _quote_found(risk["impossible_because"]["quote"], risk["impossible_because"]["source"], grounding)
        if problem:
            raise ValueError(
                f"residual_risk.impossible_because: {problem}. Without that statement the "
                "check is missing, not impossible: revise instead of approving."
            )
        self._acceptance_record(risk)

    def _acceptance_record(self, risk: dict[str, Any]) -> str:
        """The recorded acceptance of ``risk``'s check, as "<id>: <how>"; raises without one."""
        from ..core.residual_risk import ACCEPT_OPTION_LABEL, operator_accepted, same_check

        grounding = self.grounding
        if risk["accepted_by"] == "manager":
            if grounding.operator_available:
                raise ValueError(
                    "An operator is available, so accepting this risk is theirs to decide: "
                    "ask with request_review_decision (operator_need scope_or_authority, accept_risk)."
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
                    "check exactly, or revise."
                )
            return f"{wanted}: accepted by the Manager"
        if not grounding.operator_available:
            raise ValueError(
                "No operator is available in this run; only the Manager can accept this "
                "risk. Name it in unverifiable on a revise_review instead."
            )
        card = next((
            card for card in reversed(grounding.operator_decisions) if operator_accepted(card, risk["check"])
        ), None)
        if card is None:
            raise ValueError(
                "The operator has not accepted this check: only their choice of "
                f"\"{ACCEPT_OPTION_LABEL}\" on a request_review_decision raised with "
                "accept_risk naming this exact check accepts it; their other words do not. "
                "Ask them, or revise."
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


def validation_available(image: str) -> bool:
    """Whether Docker can run the configured validation image on this host."""
    image = str(image or "").strip()
    if not image or not sys.platform.startswith("linux"):
        return False
    return _docker_image_present(image)


@lru_cache(maxsize=8)
def _docker_image_present(image: str) -> bool:
    import shutil
    import subprocess

    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        result = subprocess.run(
            [docker, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def reviewer_can_execute(runner: Any) -> bool:
    """Whether this Reviewer's tools can run commands.

    Every Reviewer call is read-only (``sandbox_mode="read-only"``). Codex keeps
    its shell inside a read-only sandbox; the other backends reduce the call to
    file read and search tools. A validation image adds :data:`COMMAND_TOOL`
    on any backend (:func:`review_action_tools`), but counts only when Docker
    and the image are actually available here.
    """
    if str(getattr(runner, "backend", "")).lower() == "codex":
        return True
    from ..core.knobs import resolve_knob
    from .validation import IMAGE_ENV

    try:
        image = resolve_knob(IMAGE_ENV, "").value.strip()
    except Exception:  # noqa: BLE001 - an unreadable knob exposes no tool
        return False
    return validation_available(image)


def reviewer_evidence_mode(runner: Any, *, engineer_records_commands: bool) -> str:
    """Which evidence rule fits this Reviewer (see ``review_evidence_rule``).

    A read-only Reviewer is told to rely on host-recorded runs only when the
    Engineer's backend reports each command's exit code.
    """
    from ..core.model_visible_text import EVIDENCE_EXECUTE, EVIDENCE_READ, EVIDENCE_RECORDED

    if reviewer_can_execute(runner):
        return EVIDENCE_EXECUTE
    return EVIDENCE_RECORDED if engineer_records_commands else EVIDENCE_READ


def _readable_input_root(root: str) -> bool:
    """Is ``root`` still a directory the read-only Reviewer may be handed?"""
    from ..core.task_inputs import denied_reason
    from .validation import _read_root

    try:
        path = Path(root)
        return (
            path.is_absolute()
            and not path.is_symlink()
            and not denied_reason(path)
            and str(_read_root(root)) == str(path)
        )
    except (OSError, RuntimeError, ValueError):
        return False


@contextmanager
def review_action_tools(
    runner: Any, options: RunnerOptions, *, venue: str, venue_required: bool,
    grounding: ReviewGrounding | None = None,
) -> Iterator[tuple[ReviewActions, RunnerOptions]]:
    backend = str(getattr(runner, "backend", "")).lower()
    if backend not in {"pi", "copilot", "codex", "claude", "qoder", "memory", ""}:
        raise ValueError(f"Reviewer action tools are not supported by backend {backend!r}; no text-parser fallback is available.")
    approved_dirs = configured_read_dirs()
    # The task's own inputs (a packet mounted beside the workdir) are read-only
    # sources the Reviewer must be able to open, like the operator's approved
    # directories; without them it judged the task without its schema.
    input_dirs = [
        root for root in (grounding.input_roots if grounding is not None else ())
        if root not in approved_dirs and _readable_input_root(root)
    ]
    read_dirs = list(options.add_dirs or [])
    if backend == "copilot":
        read_dirs = list(dict.fromkeys([*read_dirs, *approved_dirs, *input_dirs]))
    elif backend in {"claude", "qoder"}:
        # Their read-only sessions (Read/Glob/Grep) reach a directory outside
        # the workdir only through --add-dir, like Copilot's.
        read_dirs = list(dict.fromkeys([*read_dirs, *input_dirs]))
    validation = configured_validation(options.working_dir, [*approved_dirs, *input_dirs])
    if validation is not None and backend == "copilot":
        read_dirs.append(str(validation.output_root))
    actions = ReviewActions(
        venue=venue, venue_required=venue_required, validation=validation, grounding=grounding,
    )
    with CallBoundBridge(
        actions.dispatch, env_prefix=PREFIX,
        on_close=validation.close if validation is not None else None,
        cancel_operation=CANCEL_TOOL,
    ) as bridge, TemporaryDirectory(prefix="argus-review-tools-") as directory:
        names = [tool["name"] for tool in actions.tools]
        environment = {**bridge.environment, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
        extra = list(options.extra_args or [])
        extensions = list(options.trusted_extensions or [])
        if backend == "pi":
            extensions.append(str(Path(__file__).with_name("pi_review_tools.mjs")))
        elif backend == "codex":
            for key, value in {
                "command": sys.executable, "args": ["-m", "argus.reviewer.tools"],
                "env_vars": list(bridge.environment),
                "env.PYTHONPATH": environment["PYTHONPATH"],
            }.items():
                extra.extend(["-c", f"mcp_servers.{SERVER}.{key}={json.dumps(value)}"])
        elif backend in {"copilot", "claude", "qoder"}:
            config = {"mcpServers": {SERVER: {
                "command": sys.executable, "args": ["-m", "argus.reviewer.tools"],
                "env": environment,
            }}}
            if backend == "copilot":
                config["mcpServers"][SERVER].update(type="local", tools=names)
            path = Path(directory) / "mcp.json"
            descriptor = os.open(path, os.O_WRONLY | getattr(os, "O_BINARY", 0) | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                json.dump(config, handle)
            extra.extend(
                ["--additional-mcp-config", "@" + str(path)] if backend == "copilot"
                else ["--mcp-config", str(path)]
            )
            names = [
                f"{SERVER}-{name}" if backend == "copilot" else f"mcp__{SERVER}__{name}"
                for name in names
            ]
        yield actions, replace(
            options, extra_args=extra, trusted_extensions=extensions,
            add_dirs=read_dirs,
            trusted_tool_names=[*(options.trusted_tool_names or []), *names],
            extension_env={**(options.extension_env or {}), **bridge.environment},
            force_safe_mode=True,
        )


def main() -> None:
    import asyncio

    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server = Server(SERVER)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(**tool) for tool in bridge_request(PREFIX, "tools", {})["tools"]]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        result = bridge_request(PREFIX, name, arguments)
        return [types.TextContent(type="text", text=json.dumps(result))]

    async def serve() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(serve())


if __name__ == "__main__":
    main()
