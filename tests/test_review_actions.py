from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.agent_cli_runner import RunnerOptions as NativeOptions
from argus.core.models import RunnerOptions
from argus.core.role_tool_bridge import bridge_request
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import PREFIX, ReviewActions, review_action_tools


def evaluate(backend, tmp_path):
    return Reviewer(backend).evaluate(
        objective="Review the current result", round_index=1, session_id=None,
        main_summary="The experiment is on the GPU.", main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )


@pytest.mark.parametrize(("action", "status", "extra"), [
    ("approve_review", "done", {}),
    ("revise_review", "continue", {}),
    ("defer_review", "continue", {}),
    ("request_review_decision", "blocked", {"question": "Which licensed dataset may we use?"}),
    ("replan_review", "replan_requested", {"authority_impact": "technical", "alternative": "Use the measured algorithm."}),
])
def test_native_action_owns_the_status_and_preserves_prose(tmp_path, action, status, extra):
    prose = "这轮代码检查通过，但实验仍在运行；不能据此宣称最终实验成功。\n\n" + "保留详细证据。" * 1600
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        message='STATUS=done\nREASON=This printed footer has no authority.',
        review_action=(action, {"review": prose, **extra}),
    ))
    result = evaluate(backend, tmp_path)
    assert result.status == status
    assert result.reason == prose
    assert not result.backend_unavailable
    assert result.next_action == ("" if status == "done" else prose)
    assert [label for label, _, _ in backend.history] == ["reviewer"]
    assert result.operator_question == extra.get("question", "")
    if action == "replan_review":
        assert result.planner_report["plan_signal"] == "reconsider"
        assert result.planner_report["alternative"] == extra["alternative"]


@pytest.mark.parametrize("text", [
    "Everything passed, the task is complete.",
    "STATUS=done\nREASON=passed",
    '{"status":"done","reason":"passed","next_action":""}',
    'ARGUS_ROLE_DECISION={"role":"reviewer","payload":{"status":"done","reason":"passed","next_action":""}}',
    'The benchmark is still running.\n{"wait_for":"subagent","wait_id":"qwen25-longbench-eval-01"}',
    "",
])
def test_text_without_a_tool_action_is_never_parsed(tmp_path, text):
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message=text))
    result = evaluate(backend, tmp_path)
    assert result.backend_unavailable
    assert result.status == "blocked"
    assert "without submitting a review action" in result.reason
    assert "STATUS" not in result.reason
    assert len(backend.history) == 1


def test_failed_turn_cannot_commit_an_earlier_approval(tmp_path):
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        review_action=("approve_review", {"review": "Checks passed."}),
        exit_code=1, fatal_error="Provider connection lost",
    ))
    result = evaluate(backend, tmp_path)
    assert result.backend_unavailable and result.status == "blocked"


def test_action_scope_is_call_bound_and_rejects_invalid_arguments():
    actions = ReviewActions()
    with pytest.raises(ValueError, match="Unknown review action"):
        actions.dispatch("complete_project", {"review": "Passed."})
    with pytest.raises(ValueError):
        actions.dispatch("approve_review", {"review": "Passed.", "status": "done"})
    with pytest.raises(ValueError):
        actions.dispatch("request_review_decision", {"review": "Choice needed.", "question": " "})
    assert actions.decision is None


@pytest.mark.parametrize("backend", ["pi", "copilot", "codex", "claude", "qoder"])
def test_tool_transport_preserves_readonly_and_keeps_capability_out_of_argv(backend):
    with review_action_tools(
        SimpleNamespace(backend=backend),
        RunnerOptions(sandbox_mode="read-only"),
        venue="", venue_required=False,
    ) as (actions, options):
        env = options.extension_env
        assert env is not None
        assert options.sandbox_mode == "read-only" and options.force_safe_mode
        assert env[f"{PREFIX}_TOKEN"] not in json.dumps(options.extra_args)
        runner = AgentCliRunner(agent_bin=backend, backend=backend)
        native_options = NativeOptions(
            sandbox_mode=options.sandbox_mode, force_safe_mode=options.force_safe_mode,
            extra_args=options.extra_args, trusted_extensions=options.trusted_extensions,
            trusted_tool_names=options.trusted_tool_names, extension_env=options.extension_env,
        )
        child_env = runner._child_env(native_options)
        assert child_env[f"{PREFIX}_TOKEN"] == env[f"{PREFIX}_TOKEN"]
        command = runner._build_command(resume_thread_id=None, options=native_options)
        assert env[f"{PREFIX}_TOKEN"] not in " ".join(command)
        if backend == "codex":
            configs = [command[index + 1] for index, flag in enumerate(command) if flag == "-c"]
            for config in configs:
                tomllib.loads(config)
            assert any("mcp_servers.argus_review_actions.env.PYTHONPATH=" in config for config in configs)
        elif backend in {"claude", "qoder"}:
            assert command[command.index("--tools") + 1] == "Read,Glob,Grep"
            assert "mcp__argus_review_actions__approve_review" in command[command.index("--allowedTools") + 1]
        elif backend == "copilot":
            assert "--allow-all-tools" not in command
            assert any("argus_review_actions(approve_review)" in value.split(",") for value in command)
        elif backend == "pi":
            assert "approve_review" in command[command.index("--tools") + 1]
        bridge_request(PREFIX, "defer_review", {"review": "Await the running experiment."}, env=env)
        assert actions.decision.status == "continue"
        assert not actions.decision.backend_unavailable


def test_action_preserves_explicit_session_and_frontier_signals():
    actions = ReviewActions()
    signal = {"kind": "quality_degradation", "target": "engineer", "detail": "Repeated an obsolete repair."}
    frontier = {"change": "risk_reduced", "resolved_obligations": ["bounded check"]}
    actions.dispatch("revise_review", {
        "review": "Use the current evidence.", "session_signal": signal,
        "frontier_report": frontier, "forward_progress": False,
    })
    assert actions.decision.session_signal == signal
    assert actions.decision.frontier_report == frontier
    assert actions.decision.planner_report["forward_progress"] is False


def test_pi_extension_invokes_the_host_action():
    with review_action_tools(
        SimpleNamespace(backend="pi"), RunnerOptions(sandbox_mode="read-only"),
        venue="", venue_required=False,
    ) as (actions, options):
        import os

        script = """
          const {default: register} = await import(process.argv[1]);
          const tools = [];
          await register({registerTool: tool => tools.push(tool)});
          const defer = tools.find(tool => tool.name === 'defer_review');
          await defer.execute('native-call', {review: 'Await the running GPU benchmark.'});
        """
        subprocess.run(
            ["node", "--input-type=module", "-e", script, options.trusted_extensions[0]],
            env={**os.environ, **options.extension_env}, check=True, capture_output=True, text=True,
        )
        assert actions.decision.status == "continue"
        assert actions.decision.reason == "Await the running GPU benchmark."


def test_mcp_tools_use_the_same_actions():
    import asyncio
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def call(options):
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "argus.reviewer.tools"],
            env={**os.environ, **options.extension_env, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "approve_review" in {tool.name for tool in tools.tools}
            result = await session.call_tool("approve_review", {"review": "The repair and its test pass."})
            assert not result.isError

    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(sandbox_mode="read-only"),
        venue="", venue_required=False,
    ) as (actions, options):
        asyncio.run(call(options))
        assert actions.decision.status == "done"


def test_unsupported_backend_has_no_text_fallback():
    with pytest.raises(ValueError, match="no text-parser fallback"):
        with review_action_tools(
            SimpleNamespace(backend="unsupported"), RunnerOptions(),
            venue="", venue_required=False,
        ):
            pytest.fail("unsupported backend must not run")
