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


@pytest.mark.parametrize(("line", "status", "question"), [
    ("→ done", "done", ""),
    ("→ engineer", "continue", ""),
    ("→ planner", "replan_requested", ""),
    ("→ manager", "blocked", ""),
    ("→ operator: Which licensed dataset may we use?", "blocked", "Which licensed dataset may we use?"),
])
def test_the_closing_line_routes_and_the_prose_is_kept(tmp_path, line, status, question):
    prose = "这轮代码检查通过，但实验仍在运行；不能据此宣称最终实验成功。\n\n" + "保留详细证据。" * 1600
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        message='STATUS=done\nREASON=This printed footer has no authority.\n→ done',
        review_action=("submit_review", {"review": f"{prose}\n{line}"}),
    ))
    result = evaluate(backend, tmp_path)
    assert result.status == status
    assert result.reason == prose
    assert not result.backend_unavailable
    assert result.next_action == ("" if status == "done" else prose)
    assert [label for label, _, _ in backend.history] == ["reviewer"]
    assert result.operator_question == question


@pytest.mark.parametrize("review", [
    "Checks passed.",
    "Checks passed.\n→ reviewer",
    "Checks passed.\n→ qa",
    "Checks passed.\n→ engineer\n→ done",
    "Checks passed.\n→ operator",
    "→ done",
])
def test_a_review_without_one_readable_handoff_is_refused(review):
    actions = ReviewActions()
    with pytest.raises(ValueError):
        actions.dispatch("submit_review", {"review": review})
    assert actions.decision is None


@pytest.mark.parametrize("text", [
    "Everything passed, the task is complete.",
    "STATUS=done\nREASON=passed",
    '{"status":"done","reason":"passed","next_action":""}',
    'ARGUS_ROLE_DECISION={"role":"reviewer","payload":{"status":"done","reason":"passed","next_action":""}}',
    'The benchmark is still running.\n{"wait_for":"subagent","wait_id":"qwen25-longbench-eval-01"}',
    "Everything passed.\n→ done",
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
        review_action=("submit_review", {"review": "Checks passed.\n→ done"}),
        exit_code=1, fatal_error="Provider connection lost",
    ))
    result = evaluate(backend, tmp_path)
    assert result.backend_unavailable and result.status == "blocked"


def test_a_refused_correction_withdraws_an_earlier_approval():
    actions = ReviewActions()
    actions.dispatch("submit_review", {"review": "Checks passed.\n→ done"})
    with pytest.raises(ValueError):
        actions.dispatch("submit_review", {"review": " \n→ engineer"})
    assert actions.decision is None


def test_a_refused_approval_leaves_an_earlier_revision_standing():
    actions = ReviewActions()
    actions.dispatch("submit_review", {"review": "Repair the date parser.\n→ engineer"})
    with pytest.raises(ValueError):
        actions.dispatch("submit_review", {"review": " \n→ done"})
    assert actions.decision.status == "continue"
    assert actions.decision.reason == "Repair the date parser."


def test_action_scope_is_call_bound_and_rejects_invalid_arguments():
    actions = ReviewActions()
    with pytest.raises(ValueError, match="Unknown review action"):
        actions.dispatch("complete_project", {"review": "Passed."})
    with pytest.raises(ValueError):
        actions.dispatch("submit_review", {"review": "Passed.\n→ done", "status": "done"})
    with pytest.raises(ValueError):
        actions.dispatch("submit_review", {"review": "Choice needed.\n→ operator:  "})
    assert actions.decision is None


@pytest.mark.parametrize("backend", ["pi", "copilot", "codex", "claude", "qoder"])
def test_tool_transport_keeps_the_engineer_s_access_and_capability_out_of_argv(backend):
    with review_action_tools(
        SimpleNamespace(backend=backend),
        RunnerOptions(full_auto=True),
        venue="", venue_required=False,
    ) as (actions, options):
        env = options.extension_env
        assert env is not None
        assert options.full_auto and options.sandbox_mode is None and not options.force_safe_mode
        assert env[f"{PREFIX}_TOKEN"] not in json.dumps(options.extra_args)
        runner = AgentCliRunner(agent_bin=backend, backend=backend)
        native_options = NativeOptions(
            full_auto=options.full_auto,
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
            assert 'mcp_servers.argus_review_actions.args=["-P", "-m", "argus.reviewer.tools"]' in configs
            assert "--full-auto" in command
        elif backend in {"claude", "qoder"}:
            assert "--tools" not in command
            assert command[command.index("--permission-mode") + 1] in {"acceptEdits", "accept_edits"}
            assert "mcp__argus_review_actions__submit_review" in command[command.index("--allowedTools") + 1]
        elif backend == "copilot":
            assert "--allow-all-tools" in command and "--available-tools" not in command
        elif backend == "pi":
            assert "--tools" not in command
        bridge_request(PREFIX, "submit_review", {"review": "Await the running experiment.\n→ engineer"}, env=env)
        assert actions.decision.status == "continue"
        assert not actions.decision.backend_unavailable


def test_action_preserves_explicit_session_signal():
    actions = ReviewActions()
    signal = {"kind": "quality_degradation", "target": "engineer", "detail": "Repeated an obsolete repair."}
    actions.dispatch("submit_review", {
        "review": "Use the current evidence.\n→ engineer", "session_signal": signal,
    })
    assert actions.decision.session_signal == signal
    assert all("frontier_report" not in tool["inputSchema"]["properties"] for tool in actions.tools)


def test_pi_extension_invokes_the_host_action():
    with review_action_tools(
        SimpleNamespace(backend="pi"), RunnerOptions(),
        venue="", venue_required=False,
    ) as (actions, options):
        import os

        script = """
          const {pathToFileURL} = await import('node:url');
          const {default: register} = await import(pathToFileURL(process.argv[1]).href);
          const tools = [];
          await register({registerTool: tool => tools.push(tool)});
          const submit = tools.find(tool => tool.name === 'submit_review');
          await submit.execute('native-call', {review: 'Await the running GPU benchmark.\\n→ engineer'});
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
            assert {tool.name for tool in tools.tools} == {"submit_review"}
            result = await session.call_tool("submit_review", {"review": "The repair and its test pass.\n→ done"})
            assert not result.isError

    with review_action_tools(
        SimpleNamespace(backend="copilot"), RunnerOptions(),
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


def test_reviewer_judges_learning_on_the_verdict():
    actions = ReviewActions()
    actions.dispatch("submit_review", {
        "review": "The fixture now proves the header.\n→ done",
        "learning": {"verdict": "worth_reflecting", "reason": "A masked display hid the value."},
    })
    assert actions.decision.learning == "worth_reflecting"
    payload = actions.decision.to_event_payload()
    assert payload["learning"] == "worth_reflecting"
    assert payload["learning_reason"] == "A masked display hid the value."


def test_judgments_are_kept_only_where_the_host_uses_them():
    actions = ReviewActions()
    assert not any("manager_attention" in tool["inputSchema"]["properties"] for tool in actions.tools)
    actions.dispatch("submit_review", {
        "review": "Repair grouping.\n→ engineer",
        "learning": {"verdict": "worth_reflecting", "reason": "Only an accepting review routes reflection."},
    })
    assert not actions.decision.learning


@pytest.mark.parametrize("line, status", [("→ done", "done"), ("→ engineer", "continue")])
@pytest.mark.parametrize("judgment", [
    {"verdict": "maybe", "reason": "?"}, "nothing_new", {"verdict": "nothing_new"},
    {"verdict": "nothing_new", "reason": "x", "extra": 1},
])
def test_a_malformed_judgment_is_ignored_and_the_verdict_stands(line, status, judgment):
    actions = ReviewActions()
    actions.dispatch("submit_review", {"review": f"Repair grouping.\n{line}", "learning": judgment})
    assert actions.decision.status == status
    assert not actions.decision.learning
    assert "learning" not in actions.decision.to_event_payload()


def test_judgments_do_not_break_the_reviewer_acceptance_shortcut():
    from argus.manager._stage_ops import _StageDecisionMixin

    actions = ReviewActions()
    actions.dispatch("submit_review", {
        "review": "Complete with evidence.\n→ done",
        "learning": {"verdict": "nothing_new", "reason": "Routine."},
    })
    assert _StageDecisionMixin._is_clean_reviewer_acceptance(actions.decision) is True
