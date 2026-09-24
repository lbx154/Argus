"""Call-bound review actions. Review prose is evidence, never a wire protocol."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import replace
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
    "approve_review": ("done", "The current task is complete; no required repair remains."),
    "revise_review": ("continue", "Return concrete in-scope repairs to Engineer."),
    "defer_review": ("continue", "Defer judgment until already-running work or missing external evidence returns. This is not failure or acceptance."),
    "request_review_decision": ("blocked", "Ask an actual operator-owned question; ordinary technical repairs use revise_review."),
    "replan_review": ("replan_requested", "Challenge the current plan or scope with evidence and a proposed alternative."),
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
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
