"""The Engineer's per-step context: what each tool step re-sends.

An agent CLI re-sends the whole session context on every tool step. These
tests pin what Argus controls about that baseline without depending on what
an earlier turn of the session held: the CLI tool surface it asks for and
copies of the same text within one prompt.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.agent_cli_runner import RunnerOptions as CliOptions
from argus.core.models import RunnerOptions
from argus.engineer.checkpoint import shared_checkpoint_instructions
from argus.engineer.round_config import EngineerConfig
from argus.life.context_packet import render_mission_brief
from argus.skills.role_library import role_skill_libraries
from argus.skills.store import SkillStore

# ── CLI tool surface ─────────────────────────────────────────────────────────


def _copilot(options: CliOptions) -> list[str]:
    runner = AgentCliRunner(agent_bin="copilot", backend="copilot")
    return runner._build_copilot_command(resume_thread_id=None, options=options)


def test_lean_copilot_surface_drops_only_unused_cli_tools() -> None:
    command = _copilot(CliOptions(lean_tool_surface=True, dangerous_yolo=True))

    excluded = next(arg for arg in command if arg.startswith("--excluded-tools="))
    assert set(excluded.split("=", 1)[1].split(",")) == {
        "sql", "fetch_copilot_cli_documentation",
    }
    # Built-in MCP servers (GitHub access) stay attached.
    assert "--disable-builtin-mcps" not in command
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
    command = backend._runner._build_copilot_command(
        resume_thread_id=None, options=options,
    )
    assert any(arg.startswith("--excluded-tools=") for arg in command)
    assert "--disable-builtin-mcps" not in command


def test_engineer_asks_for_the_lean_surface_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_LEAN_TOOLS", raising=False)
    assert EngineerConfig(model="m").lean_tool_surface is True
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_LEAN_TOOLS", "false")
    assert EngineerConfig(model="m").lean_tool_surface is False


# ── Skill discovery ──────────────────────────────────────────────────────────


def test_skill_discovery_says_to_open_skill_files_by_path(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    block = role_skill_libraries(SkillStore(root), role="engineer").block

    assert str(root.resolve()) in block
    assert "Open a chosen Skill by its path with a file-read tool" in block
    assert "No unmatched or automatically guessed bodies are injected." in block


# ── Copies of the same text within one prompt ────────────────────────────────


def test_checkpoint_block_does_not_repeat_an_index_the_prompt_already_lists(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "CHECKPOINT.md"
    full = shared_checkpoint_instructions(checkpoint, role="engineer")
    listed = shared_checkpoint_instructions(checkpoint, role="engineer", index_listed=True)

    index = str((tmp_path / "latest.json").resolve())
    assert index in full and index not in listed
    assert str(checkpoint.resolve()) in listed
    assert "another round needs" in listed
    assert "not a log or JSON verdict" in listed
    assert "Never create worktree copies" in listed


def _mission(root: Path, *, reason: str, next_action: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mission.json").write_text(json.dumps({
        "kind": "mission_context", "mission_id": "m", "stage": "delivery",
        "objective": "ship", "execution_workdir": str(root),
    }), encoding="utf-8")
    (root / "round-0001.json").write_text(json.dumps({
        "kind": "round_reviewed_handoff", "round": 1, "created_at": 1.0,
        "engineer_summary": "Implemented the CLI.",
        "review": {"status": "continue", "reason": reason, "next_action": next_action},
    }), encoding="utf-8")
    (root / "latest.json").write_text(json.dumps({
        "kind": "handoff_ref", "path": str(root / "round-0001.json"),
    }), encoding="utf-8")
    return root / "mission.json"


def test_brief_shows_a_next_action_identical_to_the_review_reason_once(
    tmp_path: Path,
) -> None:
    text = "The header still sends a literal; send the configured token."
    brief = render_mission_brief(_mission(tmp_path / "same", reason=text, next_action=text))

    assert f"- Last review: continue: {text}" in brief
    assert brief.count(text) == 1


def test_brief_keeps_a_distinct_next_action(tmp_path: Path) -> None:
    brief = render_mission_brief(_mission(
        tmp_path / "distinct",
        reason="One condition remains.",
        next_action="Exercise the public entry point.",
    ))

    assert "- Last review: continue: One condition remains." in brief
    assert "- Next action: Exercise the public entry point." in brief
