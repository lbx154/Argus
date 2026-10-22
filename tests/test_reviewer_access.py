"""The Reviewer works with the Engineer's access; its independence rests on facts.

It can run commands and reach the network exactly as the Engineer can. What it
may not do quietly is change the work it judges: the host compares the
workspace before and after the review and records any change beside the
verdict, which stands as written, for the Manager to weigh.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.agent_cli_runner import RunnerOptions as NativeOptions
from argus.core.workspace_snapshot import WorkspaceSnapshots
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import review_action_tools
from argus.skills.store import SkillStore
from argus.skills.vertical_select import persist_vertical


@pytest.fixture
def completed_reviewer_metadata(monkeypatch):
    metadata = {
        "input_tokens": 12345,
        "cached_input_tokens": 2345,
        "output_tokens": 678,
        "reasoning_output_tokens": 89,
        "premium_requests": 1.0,
        "thread_id": "synthetic-reviewer-session",
    }
    run_exec = MemoryBackend.run_exec

    def run_with_metadata(self, **kwargs):
        return replace(run_exec(self, **kwargs), **metadata)

    monkeypatch.setattr(MemoryBackend, "run_exec", run_with_metadata)
    return metadata


def _evaluate(backend, workdir, **config):
    return Reviewer(backend).evaluate(
        objective="Review the current candidate", round_index=1, session_id=None,
        main_summary="A candidate exists.", main_error=None,
        config=ReviewerConfig(working_dir=str(workdir), **config),
    )


@pytest.mark.parametrize("access", [
    {"dangerous_yolo": True, "full_auto": False},
    {"dangerous_yolo": False, "full_auto": True},
    {"dangerous_yolo": False, "full_auto": True, "sandbox_mode": "workspace-write"},
])
def test_the_reviewer_runs_with_the_engineer_s_access(tmp_path, access):
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(review_action=("submit_review", {"review": "One repair remains.\n→ engineer"})))

    _evaluate(backend, tmp_path, **access)

    options = backend.history[0][2]
    assert options.dangerous_yolo == access["dangerous_yolo"]
    assert options.full_auto == access["full_auto"]
    assert options.sandbox_mode == access.get("sandbox_mode")
    assert not options.force_safe_mode
    assert not options.isolate_workdir


def _without_verdict_tools(argv: list[str]) -> list[str]:
    """``argv`` less the plumbing that attaches the Reviewer's own verdict tools."""
    kept, index = [], 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"--additional-mcp-config", "--mcp-config", "--extension", "--allowedTools"} or (
            arg == "-c" and argv[index + 1].startswith("mcp_servers.")
        ):
            index += 2
            continue
        kept.append(arg)
        index += 1
    return kept


@pytest.mark.parametrize("backend", ["copilot", "claude", "qoder", "pi", "codex"])
@pytest.mark.parametrize("access", [{"dangerous_yolo": True}, {"full_auto": True}])
def test_reviewer_argv_is_the_engineer_s_plus_its_verdict_tools(backend, access):
    runner = AgentCliRunner(agent_bin=backend, backend=backend)
    engineer = NativeOptions(**access)
    with review_action_tools(
        SimpleNamespace(backend=backend), engineer, venue="", venue_required=False,
    ) as (_actions, reviewer):
        argv = runner._build_command(resume_thread_id=None, options=replace(
            engineer, extra_args=reviewer.extra_args, trusted_tool_names=reviewer.trusted_tool_names,
            trusted_extensions=reviewer.trusted_extensions, extension_env=reviewer.extension_env,
        ))

    assert _without_verdict_tools(argv) == runner._build_command(resume_thread_id=None, options=engineer)
    if "--allowedTools" in argv:
        allowed = argv[argv.index("--allowedTools") + 1].split(",")
        assert allowed and all(name.startswith("mcp__argus_review_actions__") for name in allowed)


def test_provider_exception_does_not_invent_completed_call_metadata(
    tmp_path, completed_reviewer_metadata,
):
    def fail(_prompt, _options):
        raise RuntimeError("synthetic provider failure")

    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        message_factory=fail,
        review_action=("submit_review", {"review": "Approval before the provider failed.\n→ done"}),
    ))
    decision = _evaluate(backend, tmp_path)
    assert decision.status == "blocked" and decision.backend_unavailable
    assert "synthetic provider failure" in decision.reason
    for name in completed_reviewer_metadata:
        assert getattr(decision, name) == (None if name == "thread_id" else 0)
    assert decision.backend_exit_code is None
    assert decision.static_fingerprint == ""
    assert decision.prompt_block_stats == {}
    assert not decision.session_resumed
    assert decision.changed_during_review == []


def _snapshots(tmp_path: Path) -> tuple[Path, WorkspaceSnapshots]:
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "model.py").write_text("ANSWER = 41\n")
    snapshots = WorkspaceSnapshots(tmp_path / "snapshots.git", workdir, "m-1")
    assert snapshots.take("baseline")
    (workdir / "model.py").write_text("ANSWER = 42\n")
    assert snapshots.take("candidate")
    return workdir, snapshots


def test_a_reviewer_that_edits_the_work_is_recorded_beside_its_verdict(tmp_path):
    workdir, snapshots = _snapshots(tmp_path)

    def edit(_prompt, _options):
        (workdir / "model.py").write_text("ANSWER = 43\n")
        (workdir / "notes.md").write_text("fixed it myself\n")
        return "Approved."

    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        message_factory=edit, review_action=("submit_review", {"review": "The answer is right.\n→ done"}),
    ))
    decision = _evaluate(backend, workdir, snapshots=snapshots)

    assert decision.status == "done" and decision.reason == "The answer is right."
    assert decision.changed_during_review == ["model.py", "notes.md"]
    assert decision.to_event_payload()["changed_during_review"] == ["model.py", "notes.md"]


def test_a_review_that_changes_nothing_says_nothing(tmp_path):
    workdir, snapshots = _snapshots(tmp_path)
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(review_action=("submit_review", {"review": "The answer is right.\n→ done"})))

    decision = _evaluate(backend, workdir, snapshots=snapshots)

    assert decision.status == "done" and decision.changed_during_review == []
    assert "changed_during_review" not in decision.to_event_payload()


def test_a_snapshot_that_cannot_be_taken_does_not_hold_up_the_review(tmp_path, monkeypatch):
    workdir, snapshots = _snapshots(tmp_path)
    monkeypatch.setattr(WorkspaceSnapshots, "take", lambda *_args, **_kwargs: False)

    def edit(_prompt, _options):
        (workdir / "model.py").write_text("ANSWER = 43\n")
        return "Approved."

    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        message_factory=edit, review_action=("submit_review", {"review": "The answer is right.\n→ done"}),
    ))
    decision = _evaluate(backend, workdir, snapshots=snapshots)

    assert decision.status == "done" and not decision.backend_unavailable
    assert decision.changed_during_review == []


def test_research_reviewer_is_granted_the_playbook_its_prompt_names(tmp_path):
    # Project s-a43d1018: 22 of 40 review rounds could not open the stage
    # playbook the prompt required, because only profile skill roots were
    # granted. The named path must reach the CLI as an --add-dir.
    persist_vertical(tmp_path, "research")
    state = tmp_path / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["current_stage"] = "experiment"
    state.write_text(json.dumps(payload), encoding="utf-8")
    backend = MemoryBackend()
    backend.backend = "copilot"
    backend.queue("reviewer", CannedResponse(
        review_action=("submit_review", {"review": "One check remains.\n→ engineer"}),
    ))

    Reviewer(backend, skill_store=SkillStore(tmp_path / "skills")).evaluate(
        objective="Review the experiment", round_index=1, session_id=None,
        main_summary="Results are ready.", main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path), active_vertical="research", full_auto=True),
    )

    _label, prompt, options = backend.history[0]
    named = Path(re.search(r"Open `([^`]+research-experiment-playbook\.md)`", prompt).group(1))
    assert named.is_file()
    argv = AgentCliRunner(agent_bin="copilot", backend="copilot")._build_command(
        resume_thread_id=None,
        options=NativeOptions(full_auto=True, add_dirs=options.add_dirs),
    )
    granted = [argv[index + 1] for index, arg in enumerate(argv) if arg == "--add-dir"]
    assert str(named.parent) in granted
    # Only the vertical's skill directory, never the package or release root.
    assert str(named.parents[3]) not in granted


@pytest.mark.parametrize("backend_name", ["copilot", "memory"])
def test_copilot_reviewer_can_read_the_files_its_prompt_names(tmp_path, backend_name):
    # Trial s-a43d1018: Copilot Reviewers were denied the Engineer log, the
    # round handoffs and checks, the goal contract, and the research playbook
    # their own prompt told them to open, then kept handing the gap back.
    from argus.skills.builtins import vertical_skill_source_path

    workdir = tmp_path / "workspaces" / "s-1"
    project_state = tmp_path / "projects" / "s-1"
    handoff = project_state / "handoffs" / "m-1"
    workdir.mkdir(parents=True)
    handoff.mkdir(parents=True)
    engineer_log = project_state / "events.jsonl"
    engineer_log.write_text("", encoding="utf-8")
    checkpoint = handoff / "CHECKPOINT.md"
    checkpoint.write_text("", encoding="utf-8")
    backend = MemoryBackend()
    backend.backend = backend_name
    backend.queue("reviewer", CannedResponse(
        review_action=("submit_review", {"review": "One repair remains.\n→ engineer"}),
    ))

    Reviewer(backend, skill_store=SkillStore(tmp_path / "skills")).evaluate(
        objective="Review the experiment round", round_index=1, session_id=None,
        main_summary="The run finished.", main_error=None,
        config=ReviewerConfig(
            working_dir=str(workdir), active_vertical="research",
            vertical_state_root=str(project_state),
        ),
        engineer_log_path=str(engineer_log), checkpoint_path=str(checkpoint),
    )

    options = backend.history[0][2]
    if backend_name != "copilot":
        assert not options.add_dirs
        return
    readable = [Path(path) for path in options.add_dirs]
    for named in (
        engineer_log,
        checkpoint,
        project_state / "goal_contract.json",
        vertical_skill_source_path("research") / "research-experiment-playbook.md",
    ):
        assert any(named.resolve().is_relative_to(root) for root in readable), named


@pytest.mark.parametrize("backend_name", ["copilot", "memory"])
def test_the_reviewer_reads_what_its_engineer_reads(tmp_path, backend_name):
    # Trial s-3f5c3427: the Reviewer was denied the recalled source the Engineer
    # had been given, and read another commit of it from GitHub instead.
    from argus.loop import SkillLoop, SkillLoopConfig

    recalled = tmp_path / "learn" / "repos"
    recalled.mkdir(parents=True)
    loop = SkillLoop(skills_dir=tmp_path / "skills", engineer_runner=object(),
                     config=SkillLoopConfig(add_dirs=[str(recalled)]))
    backend = MemoryBackend()
    backend.backend = backend_name
    backend.queue("reviewer", CannedResponse(
        review_action=("submit_review", {"review": "One repair remains.\n→ engineer"}),
    ))

    Reviewer(backend, skill_store=SkillStore(tmp_path / "skills")).evaluate(
        objective="Review the round", round_index=1, session_id=None,
        main_summary="The run finished.", main_error=None,
        config=replace(loop.supervised.reviewer_config, working_dir=str(tmp_path)),
    )

    engineer = loop.supervised.engineer_config.add_dirs
    assert str(recalled) in engineer
    assert set(engineer) <= set(backend.history[0][2].add_dirs)
