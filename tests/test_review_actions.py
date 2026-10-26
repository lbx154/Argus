from __future__ import annotations

import json
import subprocess
import tomllib
from types import SimpleNamespace

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.agent_cli_runner import RunnerOptions as NativeOptions
from argus.core.models import RunnerOptions
from argus.core.role_actions import PREFIX, role_actions
from argus.core.role_tool_bridge import bridge_request
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import review_actions
from tests.hand_off import hand_off


def evaluate(backend, tmp_path):
    return Reviewer(backend).evaluate(
        objective="Review the current result", round_index=1, session_id=None,
        main_summary="The experiment is on the GPU.", main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
    )


@pytest.mark.parametrize(("to", "status"), [
    ("done", "done"), ("engineer", "engineer"), ("planner", "planner"),
    ("manager", "manager"), ("operator", "operator"),
])
def test_the_handoff_routes_and_its_note_reaches_the_next_holder(tmp_path, to, status):
    note = "保留详细证据。" * 1600
    ask = {"ask": {"question": "Which licensed dataset may we use?", "options": []}} if to == "operator" else {}
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        message='STATUS=done\nREASON=This printed footer has no authority.\n→ done',
        action=hand_off(to, "这轮代码检查通过，但实验仍在运行。", note, **ask),
    ))
    result = evaluate(backend, tmp_path)
    assert (result.status, result.reason, result.next_action) == (status, "这轮代码检查通过，但实验仍在运行。", note)
    assert not result.backend_unavailable
    assert [label for label, _, _ in backend.history] == ["reviewer"]
    assert result.operator_question == ask.get("ask", {}).get("question", "")


def test_a_refused_correction_withdraws_the_earlier_call():
    actions = review_actions()
    actions.dispatch(*hand_off("done", "Checks passed."))
    with pytest.raises(ValueError):
        actions.dispatch(*hand_off("operator", "A choice is needed."))
    assert actions.handoff is None
    with pytest.raises(ValueError, match="Unknown action"):
        actions.dispatch("complete_project", hand_off("done")[1])


@pytest.mark.parametrize("backend", ["pi", "copilot", "codex", "claude", "qoder"])
def test_tool_transport_keeps_the_engineer_s_access_and_capability_out_of_argv(backend):
    actions = review_actions()
    with role_actions(SimpleNamespace(backend=backend), RunnerOptions(full_auto=True), actions) as options:
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
            assert any("mcp_servers.argus_actions.env.PYTHONPATH=" in config for config in configs)
            assert 'mcp_servers.argus_actions.args=["-P", "-m", "argus.core.role_actions"]' in configs
            assert 'mcp_servers.argus_actions.default_tools_approval_mode="approve"' in configs
            assert "--full-auto" in command
        elif backend in {"claude", "qoder"}:
            assert "--tools" not in command
            assert command[command.index("--permission-mode") + 1] in {"acceptEdits", "accept_edits"}
            assert "mcp__argus_actions__hand_off" in command[command.index("--allowedTools") + 1]
        elif backend == "copilot":
            assert "--allow-all-tools" in command and "--available-tools" not in command
        elif backend == "pi":
            assert "--tools" not in command
        bridge_request(PREFIX, *hand_off("engineer", "Await the running experiment."), env=env)
        assert actions.handoff.value.status == "engineer"
        assert not actions.handoff.value.backend_unavailable


def test_pi_extension_invokes_the_host_action():
    actions = review_actions()
    with role_actions(SimpleNamespace(backend="pi"), RunnerOptions(), actions) as options:
        import os

        script = """
          const {pathToFileURL} = await import('node:url');
          const {default: register} = await import(pathToFileURL(process.argv[1]).href);
          const tools = [];
          await register({registerTool: tool => tools.push(tool)});
          const submit = tools.find(tool => tool.name === 'hand_off');
          await submit.execute('native-call', {to: 'engineer', summary: 'Await the running GPU benchmark.', note: '', assumptions: []});
        """
        subprocess.run(
            ["node", "--input-type=module", "-e", script, options.trusted_extensions[0]],
            env={**os.environ, **options.extension_env}, check=True, capture_output=True, text=True,
        )
        assert actions.handoff.value.status == "engineer"
        assert actions.handoff.value.reason == "Await the running GPU benchmark."


def test_unsupported_backend_has_no_text_fallback():
    with pytest.raises(ValueError, match="cannot submit Argus actions"):
        with role_actions(SimpleNamespace(backend="unsupported"), RunnerOptions(), review_actions()):
            pytest.fail("unsupported backend must not run")


def test_reviewer_judges_learning_on_the_verdict():
    actions = review_actions()
    actions.dispatch(*hand_off(
        "done", "The fixture now proves the header.",
        learning={"verdict": "worth_reflecting", "reason": "A masked display hid the value."},
    ))
    assert actions.handoff.value.learning == "worth_reflecting"
    payload = actions.handoff.value.to_event_payload()
    assert payload["learning"] == "worth_reflecting"
    assert payload["learning_reason"] == "A masked display hid the value."


@pytest.mark.parametrize("judgment", [
    {"verdict": "maybe", "reason": "?"}, "nothing_new", {"verdict": "nothing_new"},
    {"verdict": "nothing_new", "reason": "x", "extra": 1},
])
def test_a_malformed_judgment_is_refused(judgment):
    with pytest.raises(ValueError, match="learning"):
        review_actions().dispatch(*hand_off("done", "Repair grouping.", learning=judgment))
