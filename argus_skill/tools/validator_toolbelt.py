"""Role-aware validator tool catalog for Argus research daemons.

The validators remain implemented in :mod:`argus_skill.skills.pipeline_contracts`.
This module exposes them as discoverable, role-scoped tools so agents can run
the narrowest relevant gate instead of waiting for the project-final gate.
"""

from __future__ import annotations

import argparse
import json
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

PIPELINE_CONTRACTS_MODULE = "argus_skill.skills.pipeline_contracts"
DEFAULT_PROJECT_ROOT = "."
VALIDATOR_ROLES = ("engineer", "reviewer", "critic", "planner")
FINAL_EMNLP_COMMAND = (
    "the L2 reviewer marking `done` against the full pipeline checklist"
)


@dataclass(frozen=True)
class ValidatorTool:
    """One callable validator gate exposed to Argus roles."""

    id: str
    description: str
    phase: str
    roles: tuple[str, ...]
    when_to_use: str
    cost: str = "low"
    mutates: bool = False
    final_gate: bool = False

    def command(self, project_root: str | Path = DEFAULT_PROJECT_ROOT) -> str:
        return (
            f"python -m {PIPELINE_CONTRACTS_MODULE} {self.id} "
            f"--project-root {shlex.quote(str(project_root))}"
        )

    def to_dict(self, project_root: str | Path = DEFAULT_PROJECT_ROOT) -> dict[str, object]:
        payload = asdict(self)
        payload["roles"] = list(self.roles)
        payload["command"] = self.command(project_root)
        return payload


_ALL_ROLES = VALIDATOR_ROLES
_ENGINEER_ONLY = ("engineer",)


# The validator toolbelt is intentionally empty.
#
# argus-skill used to advertise a long list of ``validate-*`` shell
# commands to the engineer / reviewer / critic / planner roles. Agents
# repeatedly tried to defeat those gates instead of producing the
# underlying evidence they were supposed to verify, and stale gate-state
# (manifest hashes, freshness JSONs, validation-priority policies, …)
# kept dragging the loop into bureaucratic repair work.
#
# The agent surface is now checklist-driven: see
# :mod:`argus_skill.skills.stage_checklists` for the per-stage items the
# L2 reviewer ticks off against artifacts. The CLI subcommand surface
# (``python -m argus_skill.skills.pipeline_contracts``) is correspondingly
# empty as far as the agent is concerned; the underlying Python validator
# functions still exist for the supervisor / harness to consume internally
# but are no longer exposed.
#
# Leaving this tuple in place (rather than deleting the module) keeps the
# old import surface usable: any caller asking
# ``format_validator_toolbelt_for_role(...)`` cleanly gets an empty string,
# and any caller iterating ``all_validator_tools()`` cleanly gets an empty
# tuple, instead of an ImportError.
VALIDATOR_TOOLS: tuple[ValidatorTool, ...] = ()


def normalize_role(role: str | None) -> str | None:
    if role is None:
        return None
    normalized = str(role).strip().lower().replace("-", "_")
    return normalized or None


def all_validator_tools() -> tuple[ValidatorTool, ...]:
    return VALIDATOR_TOOLS


def get_validator_tool(tool_id: str) -> ValidatorTool:
    for tool in VALIDATOR_TOOLS:
        if tool.id == tool_id:
            return tool
    known = ", ".join(tool.id for tool in VALIDATOR_TOOLS)
    raise KeyError(f"unknown validator tool {tool_id!r}; known tools: {known}")


def validator_tools_for_role(
    role: str | None,
    *,
    stage: str | None = None,
    include_mutating: bool = False,
) -> tuple[ValidatorTool, ...]:
    normalized_role = normalize_role(role)
    normalized_stage = str(stage).strip().lower() if stage else None
    tools: list[ValidatorTool] = []
    for tool in VALIDATOR_TOOLS:
        if normalized_role and normalized_role not in tool.roles:
            continue
        if normalized_stage and normalized_stage not in {tool.phase, tool.id}:
            continue
        if tool.mutates and not include_mutating:
            continue
        tools.append(tool)
    return tuple(tools)


def format_validator_toolbelt_for_role(
    role: str,
    *,
    stage: str | None = None,
    project_root: str | Path = DEFAULT_PROJECT_ROOT,
) -> str:
    """Return concise prompt text advertising role-relevant validator tools."""

    normalized_role = normalize_role(role) or role
    include_mutating = normalized_role == "engineer"
    tools = validator_tools_for_role(
        normalized_role,
        stage=stage,
        include_mutating=include_mutating,
    )
    if not tools:
        return ""

    lines = [
        f"## Validator toolbelt ({normalized_role})",
        (
            "Use these as callable shell tools for timely feedback. Run the narrowest "
            "validator that covers the artifact you just changed; do not wait for the "
            "project-final gate to discover local blockers."
        ),
        (
            "Narrow validators are feedback tools, not substitutes for final readiness. "
            f"Final-submission completion still requires `{FINAL_EMNLP_COMMAND}` exiting 0."
        ),
        (
            "Discover the current role-filtered list with "
            f"`python -m argus_skill.tools.validator_toolbelt list --role {normalized_role} "
            f"--project-root {project_root}`."
        ),
    ]
    if normalized_role == "engineer":
        lines.append(
            "Engineer use: run the relevant validator after each artifact-producing change "
            "and quote the command plus stdout in `## Verification (verbatim)`."
        )
    elif normalized_role == "reviewer":
        lines.append(
            "Reviewer use: if evidence is missing or stale, run the relevant validator "
            "yourself; convert issue code, path, and message into `next_action`."
        )
    elif normalized_role == "critic":
        lines.append(
            "Critic use: run narrow validators to identify high-impact residual gaps; "
            "do not propose vanity work when a gate names concrete blockers."
        )
    elif normalized_role == "planner":
        lines.append(
            "Planner use: run narrow validators when they clarify the next mission, and "
            "put the exact validator command in the task acceptance criteria."
        )

    lines.append("Callable validators:")
    for tool in tools:
        flags: list[str] = [f"phase={tool.phase}", f"cost={tool.cost}"]
        if tool.mutates:
            flags.append("mutates")
        if tool.final_gate:
            flags.append("final-gate")
        lines.append(
            f"- `{tool.command(project_root)}` — {tool.description}; "
            f"use when {tool.when_to_use}. ({', '.join(flags)})"
        )
    return "\n".join(lines)


def _print_json_tools(tools: Sequence[ValidatorTool], project_root: str | Path) -> None:
    print(json.dumps([tool.to_dict(project_root) for tool in tools], indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.tools.validator_toolbelt",
        description="List role-aware Argus validator tools backed by pipeline_contracts.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    list_parser = subcommands.add_parser("list", help="list validator tools for a role/stage")
    list_parser.add_argument("--role", choices=VALIDATOR_ROLES, help="role-specific tool view")
    list_parser.add_argument("--stage", help="optional phase or tool id filter")
    list_parser.add_argument(
        "--project-root",
        default=DEFAULT_PROJECT_ROOT,
        help="project root to render in command examples",
    )
    list_parser.add_argument(
        "--include-mutating",
        action="store_true",
        help="include mutating tools such as refresh-manifest",
    )
    list_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    args = parser.parse_args(list(argv) if argv is not None else None)
    include_mutating = bool(
        args.include_mutating or args.role is None or normalize_role(args.role) == "engineer"
    )
    tools = validator_tools_for_role(
        args.role,
        stage=args.stage,
        include_mutating=include_mutating,
    )
    if args.json:
        _print_json_tools(tools, args.project_root)
    else:
        if args.role:
            print(
                format_validator_toolbelt_for_role(
                    args.role,
                    stage=args.stage,
                    project_root=args.project_root,
                )
            )
        else:
            for tool in tools:
                print(f"{tool.id}\t{tool.phase}\t{','.join(tool.roles)}\t{tool.command(args.project_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
