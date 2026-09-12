import json
import os
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.adapters.agent_cli_backend import AgentCliBackend, _exec
from argus_skill.advisor import runtime
from argus_skill.advisor.config import save_advisor_config
from argus_skill.advisor.receipts import recent_receipts
from argus_skill.advisor.service import AdvisorService
from argus_skill.advisor.transport import AdvisorBridge, request
from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.core.usage import UsageLedger


@pytest.mark.parametrize("role", ["manager", "planner", "engineer", "reviewer"])
def test_actual_role_gateway_exposes_native_advisor_with_independent_budget_and_receipt(tmp_path, monkeypatch, role):
    """Real parent/child backend orchestration; only provider execution is fake."""
    from argus_skill.adapters.agent_cli_backend._exec_finalize import finalize_result
    from argus_skill.core import cost_control

    workspace, state, global_root = tmp_path / "workspace", tmp_path / "state", tmp_path / "global"
    workspace.mkdir()
    (workspace / "evidence.txt").write_text("One test passed; the failure path is untested.")
    save_advisor_config(state, {"enabled": True, "backend": "pi", "model": "independent/expert"})
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "main/worker")
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "on")
    admitted, settled, observed, environments = [], [], [], []

    class Reservation:
        reservation_id = "test-reservation"
        amount_usd = 0.01

        def settle(self, record):
            settled.append(record.call_id)

    def reserve(**kwargs):
        admitted.append(kwargs)
        return Reservation(), ""

    monkeypatch.setattr(cost_control, "reserve_call_budget", reserve)
    monkeypatch.setattr(_exec, "monitor_budget", lambda *_args: nullcontext())
    monkeypatch.setattr(runtime, "AdvisorService", lambda context, config: AdvisorService(
        context, config=config, backend_factory=lambda *_args: AgentCliBackend(backend="pi", runner_bin="unused-fake-pi"),
        redact=lambda text: text,
    ))
    node = shutil.which("node")
    assert node, "Node is required to verify the Pi native tool protocol"
    script = r'''
const {advisorExtension, bridgeRequest} = await import(process.argv[1]);
const Type = {Object: properties => ({type:"object", properties}),
              String: () => ({type:"string"}), Array: items => ({type:"array", items})};
let tool;
advisorExtension(bridgeRequest, Type)({registerTool: value => {tool=value;}});
if (tool.name !== "consult_advisor") throw Error("tool was not registered");
const result = await tool.execute("native-tool-call", {
  question:"Which evidence still needs verification?", evidence_refs:["evidence.txt"]
}, new AbortController().signal);
if (result.isError) throw Error(JSON.stringify(result));
process.stdout.write(JSON.stringify(result.details));
'''

    def provider(ctx, _cli_options):
        observed.append((ctx.run_label, ctx.options, ctx.call_id))
        if ctx.run_label.startswith("advisor."):
            assert ctx.options.disable_tools and not ctx.options.trusted_tool_names
            assert ctx.options.model == "independent/expert"
            assert "One test passed" in ctx.prompt
            answer = "Test the failure path referenced by workspace:evidence.txt."
        else:
            assert "consult_advisor" in ctx.options.trusted_tool_names
            assert runtime.EXTENSION in ctx.options.trusted_extensions
            assert "Independent advisor available" in ctx.prompt
            assert ctx.options.sandbox_mode == "read-only"
            command = ctx.backend._runner._build_pi_command(resume_thread_id=None, options=ctx.options)
            tools = command[command.index("--tools") + 1].split(",")
            assert "consult_advisor" in tools and "bash" not in tools and "write" not in tools
            environments.append(dict(ctx.options.extension_env))
            process = subprocess.run(
                [node, "--input-type=module", "-e", script, Path(runtime.EXTENSION).with_name("pi_tools.mjs").as_uri()],
                capture_output=True, text=True, timeout=10,
                env={"PATH": os.defpath, **ctx.options.extension_env},
            )
            assert process.returncode == 0, process.stderr
            receipt = json.loads(process.stdout)
            assert receipt["status"] == "completed" and receipt["parent_call_id"] == ctx.call_id
            assert receipt["caller_role"] == role and "evidence" not in receipt
            answer = "I will verify that missing case before deciding."
        return finalize_result(ctx, RunnerResult(
            exit_code=0, agent_messages=[answer], usage_model=ctx.options.model,
            input_tokens=20, input_tokens_present=True, output_tokens=7, output_tokens_present=True,
        ), status="completed")

    monkeypatch.setattr(_exec, "spawn_and_finish", provider)
    backend = AgentCliBackend(backend="pi", runner_bin="unused-fake-pi")
    backend.set_usage_context(project_root=state, global_root=global_root, mission_id="mission-owned")
    original = RunnerOptions(model="main/worker", working_dir=str(workspace), sandbox_mode="read-only")
    result = backend.run_exec(prompt="Use current evidence.", options=original, run_label=f"{role}.turn")
    assert result.exit_code == 0
    assert original.trusted_tool_names is None and original.extension_env is None
    assert len(observed) == 2 and observed[0][2] != observed[1][2]
    assert [item["model"] for item in admitted] == ["main/worker", "independent/expert"]
    assert all(item["mission_id"] == "mission-owned" and item["global_root"] == global_root for item in admitted)
    records = UsageLedger(state, migrate_legacy=False).records()
    assert len(records) == 2 and len(settled) == 2
    assert all(row.mission_id == "mission-owned" for row in records)
    receipts = recent_receipts(state)
    assert len(receipts) == 1 and receipts[0]["call_id"] in {row.call_id for row in records}
    with pytest.raises(OSError):
        request("receipt", {"consultation_id": receipts[0]["consultation_id"]}, env=environments[0])


@pytest.mark.parametrize("label,backend,disabled", [
    ("advisor.manager.nested", "pi", False), ("planner.cycle1", "pi", True),
    ("reviewer.turn", "codex", False),
])
def test_no_recursive_tool_or_shell_fallback_for_read_only_roles(tmp_path, label, backend, disabled):
    save_advisor_config(tmp_path, {"enabled": True, "backend": "pi", "model": "independent/expert"})
    options = RunnerOptions(working_dir=str(tmp_path), sandbox_mode="read-only", disable_tools=disabled)
    ctx = SimpleNamespace(options=options, run_label=label, usage_project_root=tmp_path,
                          backend=SimpleNamespace(_backend_name=backend), prompt="original")
    with runtime.advisor_run(ctx):
        assert ctx.options is options and ctx.prompt == "original"


def test_tool_bridge_rejects_context_forgery_and_cli_uses_bound_scope(tmp_path, monkeypatch, capsys):
    from argus_skill.advisor.config import AdvisorConfig
    from argus_skill.advisor.service import AdvisorCallContext
    from argus_skill.tools.advisor import main

    class Backend:
        def set_usage_context(self, **_kwargs):
            pass

        def run_exec(self, **_kwargs):
            return RunnerResult(exit_code=0, agent_messages=["advice"], usage_model="expert")

    service = AdvisorService(
        AdvisorCallContext(tmp_path, tmp_path, "engineer", "owned-parent"),
        config=AdvisorConfig(enabled=True, backend="codex", model="expert"),
        backend_factory=lambda *_args: Backend(), redact=lambda text: text, emit=lambda _event: True,
    )
    with AdvisorBridge(service) as bridge:
        with pytest.raises(ValueError, match="host-owned"):
            request("consult", {"question": "Question", "project_root": "/another-project"}, env=bridge.environment)
        for name, value in bridge.environment.items():
            monkeypatch.setenv(name, value)
        assert main(["consult", "--question", "Question", "--request-id", "cli-request"]) == 0
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["parent_call_id"] == "owned-parent" and receipt["status"] == "completed"
