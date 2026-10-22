"""The call-bound review action. Review prose is evidence; its closing baton line routes."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator, Mapping

from jsonschema import ValidationError, validate

from ..core.model_visible_text import FIXTURE_EVIDENCE_RULE
from ..core.models import ReviewDecision, RunnerOptions
from ..core.operator_decision import normalize_agent_options
from ..core.research_contract import RESULT_FIELD_CHOICES, normalize_research_result
from ..core.role_handoff import (
    HANDOFF_STATUS,
    OPERATOR_CHOICES_RULE,
    read_baton_line,
    strip_handoff,
)
from ..core.role_tool_bridge import CallBoundBridge, bridge_request
from ..core.venue_review import ACCEPTED_RECOMMENDATIONS, RECOMMENDATIONS

PREFIX = "ARGUS_PLUGIN_REVIEW"
SERVER = "argus_review_actions"
#: The one review action. The review names the next holder in its closing line,
#: as every handoff does (docs/role-handoff-design.md, "The baton"); the fields
#: beside it are the payloads a side effect needs.
ACTION = "submit_review"
HANDOFF_LINE = (
    "End the review with one line naming who holds the work next: `→ done` "
    "accepts this candidate; `→ engineer` returns concrete in-scope repairs; "
    "`→ planner` the task or plan is wrong; `→ manager` an obstacle or a conflict "
    "in the acceptance criteria only the Manager can settle; `→ operator: <one "
    "question>` a decision only the operator can make."
)


#: An optional routing judgment on the accepting verdict; a malformed one is
#: ignored, never fatal.
_LEARNING_FIELD: dict[str, Any] = {
    "type": "object",
    "description": (
        "Is anything from this task worth keeping for later work: a failure, a "
        "correction, a surprise, a new technique, a reusable pattern? When "
        "unsure, worth_reflecting."
    ),
    "properties": {"verdict": {"enum": ["worth_reflecting", "nothing_new"]}, "reason": {"type": "string"}},
    "required": ["verdict", "reason"], "additionalProperties": False,
}


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


def _basis_field() -> dict[str, Any]:
    return {
        "type": "object",
        "description": (
            "Required when you call a check impossible here because what it needs "
            "exists only at grading or deploy time: the task, packet or environment "
            "statement that says so, quoted verbatim, and its source ('task', or a "
            "workspace file the packet names or unchanged since work on this objective "
            "began; never one written or edited since). The host checks the quote. Without such a statement "
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
        grounding: ReviewGrounding | None = None,
    ) -> None:
        from ..core.autonomy import operator_questions_can_be_asked

        self.venue = venue
        self.venue_required = venue_required
        self.grounding = grounding or ReviewGrounding(
            operator_available=operator_questions_can_be_asked()
        )
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
        fields: dict[str, Any] = {
            "review": {
                "type": "string", "minLength": 1,
                "description": (
                    "Your complete natural-language review, including evidence and what "
                    "the next holder needs. " + HANDOFF_LINE
                ),
            },
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
            "learning": _LEARNING_FIELD,
            # A replanning Reviewer can be stopped by the same obstacle; one
            # task replanned four times over a token only its grader receives.
            "unverifiable": {
                "type": "string",
                "description": (
                    "Not with `→ done`: a decisive check that cannot happen here, and the "
                    "risk left. " + (
                        "Leaving it unverified is the operator's decision: ask them with "
                        "`→ operator: <question>` and accept_risk."
                        if self.grounding.operator_available else
                        "The operator cannot be asked, so the Manager sees this and "
                        "decides whether to accept that risk."
                    )
                ),
            },
            "impossible_because": _basis_field(),
            "residual_risk": {
                "type": "object",
                "description": (
                    "Only with `→ done`, when one decisive check stays unverified because "
                    "it is impossible here and leaving it so was accepted: by the operator "
                    "choosing to accept it on the question you raised with accept_risk, "
                    "or, in a run with no operator, by the Manager (cite the risk id "
                    "listed under Accepted residual risk). Name the check exactly as "
                    "accepted. Without that acceptance, do not accept the candidate. "
                    "The evidence you judged it from must be something you read yourself. "
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
            },
            "accept_risk": {
                "type": "object",
                "description": (
                    "With `→ operator`, when the question is whether to leave one check "
                    "impossible here unverified: name the check and the risk. The host adds "
                    "the choice to accept that risk or keep the check; only choosing it accepts."
                ),
                "properties": {
                    "check": {"type": "string", "minLength": 3},
                    "risk": {"type": "string", "minLength": 3},
                },
                "required": ["check", "risk"],
                "additionalProperties": False,
            },
        }
        if self.venue_required:
            fields["recommendation"] = {
                "type": "string", "enum": list(RECOMMENDATIONS),
                "description": (
                    f"Your actual current recommendation for {self.venue}, not a future "
                    "aspiration. Required with `→ done` and `→ engineer`."
                ),
            }
        return [{
            "name": ACTION,
            "description": (
                "Submit your judgment of this candidate. `→ done` accepts it only after you "
                "checked each requirement and symptom the task (this increment) states, and "
                "each acceptance criterion, against the deliverable: tests the Engineer wrote "
                "show the code matches its reading of the task, not that the reading is "
                "right, and a stated symptom called intended or out of scope needs support "
                "in the task text itself."
            ),
            "inputSchema": {
                "type": "object", "properties": fields, "required": ["review"],
                "additionalProperties": False,
            },
        }]

    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "tools":
            return {"tools": self.tools}
        if action != ACTION:
            raise ValueError(f"Unknown review action: {action}")
        if self.decision is not None and self.decision.status == "done":
            # A new verdict replaces an acceptance. If this one is refused, the
            # turn must not settle on the acceptance it was correcting; any other
            # earlier verdict keeps the round open and may stand.
            self.decision = None
        schema = self.tools[0]["inputSchema"]
        payload = dict(payload)
        if "learning" in payload:
            try:
                validate(payload["learning"], _LEARNING_FIELD)
            except ValidationError:
                # Absent rather than fatal: the verdict itself stands, and an
                # absent judgment means reflection runs.
                payload.pop("learning")
        try:
            validate(payload, schema)
        except ValidationError as exc:
            raise ValueError(exc.message) from exc
        review = payload["review"]
        prose = strip_handoff(review).strip()
        if not prose:
            raise ValueError("The review must explain the judgment.")
        handoff = read_baton_line(review)
        holder = handoff.holder if handoff is not None else ""
        if holder not in HANDOFF_STATUS:
            raise ValueError(f"{HANDOFF_LINE} {OPERATOR_CHOICES_RULE}")
        if holder != "done" and "residual_risk" in payload:
            raise ValueError("residual_risk records an accepted risk: it goes with `→ done`.")
        if holder == "done" and ("unverifiable" in payload or "impossible_because" in payload):
            raise ValueError("A check left unverified on `→ done` is a residual_risk.")
        if holder != "operator" and "accept_risk" in payload:
            raise ValueError("accept_risk belongs to a question: end with `→ operator: <question>`.")
        if self.venue_required and holder in {"done", "engineer"} and "recommendation" not in payload:
            raise ValueError(f"State your current recommendation for {self.venue}.")
        research = normalize_research_result(payload.get("research_result"))
        if "research_result" in payload and research is None:
            raise ValueError("The research assessment is incomplete.")
        self._check_grounding(payload)
        status = HANDOFF_STATUS[holder]
        options = handoff.options
        if isinstance(payload.get("accept_risk"), dict):
            from ..core.residual_risk import acceptance_options

            options = acceptance_options(payload["accept_risk"]["check"], payload["accept_risk"]["risk"])
        decision = ReviewDecision(
            status=status, reason=prose,
            next_action="" if status == "done" else prose,
            operator_question=handoff.question,
            operator_options=normalize_agent_options(options),
            research_result=research,
            session_signal=payload.get("session_signal", {}),
        )
        judged = payload.get("learning")
        if holder == "done" and isinstance(judged, dict):
            decision.learning = judged["verdict"]
            decision.learning_reason = str(judged.get("reason") or "")[:500]
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
        if "recommendation" in payload:
            recommendation = payload["recommendation"]
            decision.venue_review = {
                "venue": self.venue, "recommendation": recommendation,
                "acceptance_clear": recommendation in ACCEPTED_RECOMMENDATIONS,
                "rationale": prose, "blocking_issues": [],
                "revision_required": holder != "done",
            }
        # A native action belongs to this call. Prose, tool output printed in a
        # message, and saved project files cannot manufacture this assignment.
        self.decision = decision
        return {"recorded": holder}

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
                "check is missing, not impossible: return it to the Engineer instead of accepting."
            )
        self._acceptance_record(risk)

    def _acceptance_record(self, risk: dict[str, Any]) -> str:
        """The recorded acceptance of ``risk``'s check, as "<id>: <how>"; raises without one."""
        from ..core.residual_risk import ACCEPT_OPTION_LABEL, operator_accepted, same_check

        grounding = self.grounding
        if risk["accepted_by"] == "manager":
            if grounding.operator_available:
                raise ValueError(
                    "The operator can be asked, so accepting this risk is theirs to decide: "
                    "ask them with `→ operator: <question>` and accept_risk."
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


@contextmanager
def review_action_tools(
    runner: Any, options: RunnerOptions, *, venue: str, venue_required: bool,
    grounding: ReviewGrounding | None = None,
) -> Iterator[tuple[ReviewActions, RunnerOptions]]:
    backend = str(getattr(runner, "backend", "")).lower()
    if backend not in {"pi", "copilot", "codex", "claude", "qoder", "memory", ""}:
        raise ValueError(f"Reviewer action tools are not supported by backend {backend!r}; no text-parser fallback is available.")
    actions = ReviewActions(venue=venue, venue_required=venue_required, grounding=grounding)
    with (
        CallBoundBridge(actions.dispatch, env_prefix=PREFIX) as bridge,
        TemporaryDirectory(prefix="argus-review-tools-") as directory,
    ):
        names = [tool["name"] for tool in actions.tools]
        environment = {**bridge.environment, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
        extra = list(options.extra_args or [])
        extensions = list(options.trusted_extensions or [])
        if backend == "pi":
            extensions.append(str(Path(__file__).with_name("pi_review_tools.mjs")))
        elif backend == "codex":
            for key, value in {
                "command": sys.executable, "args": ["-P", "-m", "argus.reviewer.tools"],
                "env_vars": list(bridge.environment),
                "env.PYTHONPATH": environment["PYTHONPATH"],
            }.items():
                extra.extend(["-c", f"mcp_servers.{SERVER}.{key}={json.dumps(value)}"])
        elif backend in {"copilot", "claude", "qoder"}:
            config = {"mcpServers": {SERVER: {
                "command": sys.executable, "args": ["-P", "-m", "argus.reviewer.tools"],
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
            trusted_tool_names=[*(options.trusted_tool_names or []), *names],
            extension_env={**(options.extension_env or {}), **bridge.environment},
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
