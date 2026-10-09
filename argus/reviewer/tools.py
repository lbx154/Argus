"""Call-bound review actions. Review prose is evidence, never a wire protocol."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from jsonschema import ValidationError, validate

from ..core.models import ReviewDecision, ReviewStatus, RunnerOptions
from ..core.operator_decision import normalize_agent_options
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


class ReviewActions:
    def __init__(
        self, *, venue: str = "", venue_required: bool = False,
        validation: ReviewValidation | None = None,
    ) -> None:
        self.venue = venue
        self.venue_required = venue_required
        self.validation = validation
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
            if _ACTIONS[name][0] == "continue":
                fields["unverifiable"] = {
                    "type": "string",
                    "description": (
                        "Only when the fact you dispute is hidden from every view you "
                        "have (e.g. masked or redacted display), so repeating the finding "
                        "cannot settle it: name the fact and the rerunnable check that "
                        "would. The Manager sees this."
                    ),
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
        status = _ACTIONS[action][0]
        decision = ReviewDecision(
            status=status, reason=review,
            next_action="" if status == "done" else review,
            operator_question=question,
            operator_options=normalize_agent_options(payload.get("options", [])),
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


@contextmanager
def review_action_tools(
    runner: Any, options: RunnerOptions, *, venue: str, venue_required: bool,
) -> Iterator[tuple[ReviewActions, RunnerOptions]]:
    backend = str(getattr(runner, "backend", "")).lower()
    if backend not in {"pi", "copilot", "codex", "claude", "qoder", "memory", ""}:
        raise ValueError(f"Reviewer action tools are not supported by backend {backend!r}; no text-parser fallback is available.")
    approved_dirs = configured_read_dirs()
    read_dirs = list(options.add_dirs or [])
    if backend == "copilot":
        read_dirs = list(dict.fromkeys([*read_dirs, *approved_dirs]))
    validation = configured_validation(options.working_dir, approved_dirs)
    if validation is not None and backend == "copilot":
        read_dirs.append(str(validation.output_root))
    actions = ReviewActions(venue=venue, venue_required=venue_required, validation=validation)
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
