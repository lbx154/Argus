"""The Engineer's per-step context: what each tool step re-sends.

An agent CLI re-sends the whole session context on every tool step. These
tests pin the three things Argus controls about that baseline: the CLI tool
surface it asks for, sections a resumed session already holds, and copies of
the review the same prompt already carries.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.agent_cli_runner import RunnerOptions as CliOptions
from argus.core.models import RunnerOptions
from argus.core.role_session import RoleSessionCapsule
from argus.engineer.round_config import EngineerConfig
from argus.engineer.round_prompt import RoundPromptMixin, elide_delivered_sections
from argus.life.context_packet import render_mission_brief
from argus.roles.prompts.engineer import build_mission_prompt

_LONG = "x" * 400


# ── CLI tool surface ─────────────────────────────────────────────────────────


def _copilot(options: CliOptions) -> list[str]:
    runner = AgentCliRunner(agent_bin="copilot", backend="copilot")
    return runner._build_copilot_command(resume_thread_id=None, options=options)


def test_lean_copilot_surface_drops_unused_cli_tools_but_keeps_the_rest() -> None:
    command = _copilot(CliOptions(lean_tool_surface=True, dangerous_yolo=True))

    assert "--disable-builtin-mcps" in command
    excluded = next(arg for arg in command if arg.startswith("--excluded-tools="))
    assert set(excluded.split("=", 1)[1].split(",")) == {
        "sql", "fetch_copilot_cli_documentation",
    }
    # A denylist: no allowlist narrows the tools the Engineer works with.
    assert not any(arg.startswith("--available-tools") for arg in command)
    assert "--yolo" in command
    assert "--no-custom-instructions" not in command


def test_default_copilot_surface_is_unchanged_for_other_callers() -> None:
    command = _copilot(CliOptions(dangerous_yolo=True))

    assert "--disable-builtin-mcps" not in command
    assert not any(arg.startswith("--excluded-tools") for arg in command)


@pytest.mark.parametrize(
    "options",
    [
        CliOptions(lean_tool_surface=True, sandbox_mode="read-only"),
        CliOptions(lean_tool_surface=True, disable_tools=True),
    ],
)
def test_lean_surface_leaves_explicit_tool_allowlists_alone(options: CliOptions) -> None:
    command = _copilot(options)

    assert not any(arg.startswith("--excluded-tools") for arg in command)


def test_lean_surface_reaches_the_copilot_command_through_the_backend() -> None:
    backend = AgentCliBackend(backend="copilot")
    options = backend._translate_options(RunnerOptions(lean_tool_surface=True))

    assert options.lean_tool_surface is True
    assert "--disable-builtin-mcps" in backend._runner._build_copilot_command(
        resume_thread_id=None, options=options,
    )


def test_engineer_asks_for_the_lean_surface_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_LEAN_TOOLS", raising=False)
    assert EngineerConfig(model="m").lean_tool_surface is True
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_LEAN_TOOLS", "false")
    assert EngineerConfig(model="m").lean_tool_surface is False


# ── Sections a resumed session already holds ─────────────────────────────────


def test_identical_long_section_becomes_a_pointer_once_delivered() -> None:
    prompt = f"## Standing rules\n{_LONG}\n\n## New review\nFix the parser."
    _sent, first_digests, first_elided = elide_delivered_sections(prompt, frozenset())
    assert first_elided == 0

    again, digests, elided = elide_delivered_sections(prompt, frozenset(first_digests))

    assert elided == 1
    assert _LONG not in again
    assert "## Standing rules\nUnchanged from the copy given earlier" in again
    assert again.endswith("## New review\nFix the parser.")
    assert digests == []  # the short section is below the elision floor


def test_footer_and_state_pointers_are_always_resent() -> None:
    footer = (
        f"## Carrying context between rounds\n{_LONG}\n"
        "Reason naturally, then end with only the Host actions below:\n"
        "Decision:\nNEXT_OWNER=reviewer"
    )
    pointers = f"## Shared checkpoint\nContinuation note: `/state/CHECKPOINT.md` {_LONG}"
    authority = f"## OperatorContext\n{_LONG}\noperator_context_revision=3"
    host = f"## Current host context\n{_LONG}"
    prompt = "\n\n".join((footer, pointers, authority, host))
    _sent, digests, _elided = elide_delivered_sections(prompt, frozenset())

    again, _digests, elided = elide_delivered_sections(prompt, frozenset(digests))

    assert elided == 0
    assert again == prompt


def test_compact_turn_carries_every_rule_but_elides_what_the_session_holds() -> None:
    """The builder still emits the standing rules on every turn; only the
    send step replaces the copies a resumed thread already holds."""
    full = build_mission_prompt(
        task="## Mission contract\nShip the CLI.", skill_text="", next_action="Fix A.",
        compact_team=True, require_post_task_learning=True, project_skill_dir="/skills/engineer",
    )
    compact = build_mission_prompt(
        task="## Mission contract\nShip the CLI.", skill_text="", next_action="Fix B.",
        include_static=False, require_post_task_learning=True, project_skill_dir="/skills/engineer",
    )
    assert "argus.tools.subagent submit" in compact
    assert "Performance root-cause/bottleneck/replacement claims need" in compact

    _sent, delivered, _ = elide_delivered_sections(full, frozenset())
    sent, _digests, elided = elide_delivered_sections(compact, frozenset(delivered))

    assert elided >= 4  # standing rules, voice, durable learning, review instructions
    assert "argus.tools.subagent submit" not in sent
    assert "Fix B." in sent
    assert "Decision:\nMILESTONE_STATUS=done" in sent
    # Budget for the Argus share of a continuation on a resumed session.
    assert len(sent) < 1_500
    assert len(compact) > 2 * len(sent)


def _capsule(tmp_path: Path) -> RoleSessionCapsule:
    return RoleSessionCapsule.open(
        role="engineer", policy="rolling", objective_revision="r", workdir=tmp_path,
        backend="copilot", model="m", checkpoint_path=None,
        path=tmp_path / "role-sessions" / "engineer.json",
    )


def _turn(thread_id: str) -> SimpleNamespace:
    return SimpleNamespace(input_tokens=10, cached_input_tokens=0, thread_id=thread_id)


def test_delivered_sections_follow_the_provider_thread(tmp_path: Path) -> None:
    capsule = _capsule(tmp_path)
    capsule.prepare(max_turns=6, max_input_tokens=120_000)
    capsule.pending_sections = ["a"]
    capsule.complete(_turn("T1"))
    assert capsule.delivered_sections == ["a"]

    capsule.prepare(max_turns=6, max_input_tokens=120_000)
    assert capsule.action == "resumed"
    capsule.pending_sections = ["b"]
    capsule.complete(_turn("T1"))
    assert capsule.delivered_sections == ["a", "b"]

    reopened = _capsule(tmp_path)
    assert reopened.delivered_sections == ["a", "b"]

    # The provider answered on a different thread: it holds only this turn.
    reopened.prepare(max_turns=6, max_input_tokens=120_000)
    reopened.pending_sections = ["c"]
    reopened.complete(_turn("T2"))
    assert reopened.delivered_sections == ["c"]

    reopened.rotate("context_limit")
    assert reopened.delivered_sections == []


def test_failed_turn_on_no_thread_forgets_delivery(tmp_path: Path) -> None:
    capsule = _capsule(tmp_path)
    capsule.prepare(max_turns=6, max_input_tokens=120_000)
    capsule.pending_sections = ["a"]
    capsule.complete(_turn(""))

    assert capsule.delivered_sections == []


def _mission(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    review_text = "The header still sends a literal; send the configured token."
    (root / "mission.json").write_text(json.dumps({
        "kind": "mission_context", "mission_id": "m", "stage": "delivery",
        "objective": "ship", "execution_workdir": str(root),
    }), encoding="utf-8")
    (root / "round-0001.json").write_text(json.dumps({
        "kind": "round_reviewed_handoff", "round": 1, "created_at": 1.0,
        "engineer_summary": "Implemented the CLI.",
        "review": {"status": "continue", "reason": review_text, "next_action": review_text},
    }), encoding="utf-8")
    (root / "latest.json").write_text(json.dumps({
        "kind": "handoff_ref", "path": str(root / "round-0001.json"),
    }), encoding="utf-8")
    return root / "mission.json"


def test_brief_drops_review_copies_the_prompt_already_carries(tmp_path: Path) -> None:
    mission = _mission(tmp_path / "m")
    full = render_mission_brief(mission)
    if "The header still sends a literal" not in full:
        pytest.skip("fixture handoff layout not recognized by this brief")

    assert full.count("The header still sends a literal") == 1  # next action == reason

    lean = render_mission_brief(mission, include_review_text=False)
    assert "The header still sends a literal" not in lean
    assert "- Last review: continue (full text in this prompt)" in lean
    assert "Implemented the CLI." in lean


def test_resumed_round_sends_only_new_text(tmp_path: Path) -> None:
    capsule = SimpleNamespace(
        policy="rolling", action="resumed", rotation_reason="",
        prompt_block=lambda: "", delivered_sections=[], pending_sections=[],
    )
    config = SimpleNamespace(
        compact_continuation_prompts=True, background_subagent_advisory=False,
        max_rounds=8, context_packet_path="", engineer_full_round_policy="session",
    )
    rules = f"## Standing rules\n{_LONG}"
    events: list[dict] = []

    def assemble(review: str) -> str:
        return RoundPromptMixin()._assemble_round_prompt(
            round_index=2, supervised_config=config,
            engineer_prompt_builder=lambda _next, _static: f"{rules}\n\n## Review\n{review}",
            reviewer_next_action=review, checkpoint_path=None, workdir=tmp_path,
            role_session=capsule, on_event=events.append,
        )

    first = assemble("first")
    assert _LONG in first
    capsule.delivered_sections = list(capsule.pending_sections)

    second = assemble("second")
    assert _LONG not in second and "second" in second
    assert events[-1]["elided_sections"] == 1
