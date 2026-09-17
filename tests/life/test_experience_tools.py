from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.models import RunnerOptions
from argus.life import experience_runtime
from argus.life.experience_tools import ExperienceBridge, ExperienceToolService, request
from argus.life.failure_experience import FailureExperience, StaleFailureExperienceWrite
from argus.tools.experience import main


def capsule(service: ExperienceToolService):
    service.workspace.mkdir(parents=True, exist_ok=True)
    (service.workspace / "evidence.txt").write_text("The controlled retry measured a different boundary condition.")
    return service.store.append(FailureExperience.new(
        mission_id="mission-a", title="quartz experiment", objective="quartz behavior",
        status="failed", factual_outcome="original interpretation", evidence_refs=["review:original"],
    ))


def correction(identity: str, revision: int = 1) -> dict:
    return {"experience_id": identity, "expected_revision": revision,
            "evidence_refs": ["workspace:evidence.txt"], "reason": "The controlled retry corrects the interpretation.",
            "changes": {"factual_outcome": "corrected observation", "lessons": ["Recheck the boundary conditions."]}}


def test_cli_revises_same_identity_and_retracts_through_call_bound_bridge(tmp_path, monkeypatch, capsys):
    service = ExperienceToolService(tmp_path, role="engineer", parent_call_id="call-1")
    original = capsule(service)
    with ExperienceBridge(service) as bridge:
        for key, value in bridge.environment.items():
            monkeypatch.setenv(key, value)
        assert main(["search", "quartz"]) == 0
        assert json.loads(capsys.readouterr().out)["experiences"][0]["id"] == original.id
        assert main(["get", original.id]) == 0
        assert json.loads(capsys.readouterr().out)["experience"]["revision"] == 1
        assert main(["revise", original.id, "--expected-revision", "1", "--evidence-ref", "workspace:evidence.txt",
                     "--reason", "new measurement", "--changes", '{"factual_outcome":"corrected observation"}']) == 0
        assert json.loads(capsys.readouterr().out)["revision"] == 2
        assert service.store.get(original.id).annotations[-1].relation == "correction by engineer; call:call-1"
        assert main(["retract", original.id, "--expected-revision", "2", "--evidence-ref", "workspace:evidence.txt",
                     "--reason", "measurement withdrawn"]) == 0
        assert json.loads(capsys.readouterr().out)["state"] == "retracted"
        assert not service.store.recent()
    with pytest.raises(OSError):
        request("get", {"experience_id": original.id}, env=bridge.environment)


def test_host_project_scope_evidence_and_revision_cannot_be_overridden(tmp_path):
    service = ExperienceToolService(tmp_path / "one", role="manager", parent_call_id="call")
    other = ExperienceToolService(tmp_path / "two", role="manager", parent_call_id="call")
    original = capsule(service)
    foreign = capsule(other)
    assert service.dispatch("get", {"experience_id": foreign.id})["experience"] is None
    for injected in ({"project_root": str(other.root)}, {"role": "manager"}, {"parent_call_id": "forged"}):
        with pytest.raises(ValueError, match="host-owned"):
            service.dispatch("revise", {**correction(original.id), **injected})
    with pytest.raises(ValueError, match="evidence"):
        service.dispatch("revise", {**correction(original.id), "evidence_refs": []})
    with pytest.raises(ValueError, match="content fields"):
        service.dispatch("revise", {**correction(original.id), "changes": {"status": "completed"}})
    service.dispatch("revise", correction(original.id))
    with pytest.raises(StaleFailureExperienceWrite):
        service.dispatch("revise", correction(original.id))
    assert service.store.get(original.id).revision == 2
    assert other.store.get(foreign.id).revision == 1


def test_reviewer_remains_read_only_even_with_forged_writable_request(tmp_path):
    service = ExperienceToolService(tmp_path, role="reviewer", parent_call_id="review", writable=True)
    original = capsule(service)
    assert service.dispatch("search", {"query": "quartz"})["experiences"]
    with pytest.raises(ValueError, match="cannot mutate"):
        service.dispatch("revise", correction(original.id))
    with pytest.raises(ValueError, match="cannot mutate"):
        service.dispatch("retract", {key: value for key, value in correction(original.id).items() if key != "changes"})
    assert service.store.get(original.id).revision == 1


def test_replaced_project_alias_cannot_change_tool_namespace(tmp_path):
    original = tmp_path / "project"
    original.mkdir()
    service = ExperienceToolService(original, role="manager", parent_call_id="call")
    item = capsule(service)
    moved = tmp_path / "original"
    original.rename(moved)
    other = tmp_path / "foreign"
    other.mkdir()
    original.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="root changed"):
        service.dispatch("get", {"experience_id": item.id})
    assert not (other / "failure_experiences.jsonl").exists()


@pytest.mark.parametrize("name", ["failure_experiences.jsonl", "failure_experiences.jsonl.lock",
                                  "failure_experiences.sqlite3", "embedding/config.json"])
def test_child_file_alias_cannot_read_another_projects_source_or_index(tmp_path, name):
    service = ExperienceToolService(tmp_path / "one", role="manager", parent_call_id="call")
    foreign = ExperienceToolService(tmp_path / "two", role="manager", parent_call_id="call")
    private = capsule(foreign)
    alias = service.root / name
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(foreign.root / "failure_experiences.jsonl")
    with pytest.raises(ValueError, match="canonical regular"):
        service.dispatch("get", {"experience_id": private.id})
    with pytest.raises(ValueError, match="canonical regular"):
        service.dispatch("search", {"query": "quartz"})
    assert foreign.store.get(private.id).revision == 1


def test_file_evidence_receipts_record_real_scoped_bytes_without_claiming_truth(tmp_path):
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    service = ExperienceToolService(state, workspace=workspace, role="engineer", parent_call_id="call",
                                    redact=lambda text: text.replace("test-secret", "[redacted]"))
    item = capsule(service)
    raw = b"Observed one condition. test-secret"
    (workspace / "evidence.txt").write_bytes(raw)
    receipt = service.dispatch("revise", correction(item.id))
    row, = receipt["evidence"]
    assert row["ref"] == "workspace:evidence.txt" and row["bytes_read"] == len(raw)
    assert row["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert row["sha256"] == hashlib.sha256(raw.decode().replace("test-secret", "[redacted]").encode()).hexdigest()
    assert "test-secret" not in json.dumps(receipt) and "text" not in row
    assert "interpretation remains advisory" in receipt["evidence_verification"]
    revised = service.store.get(item.id)
    assert f"workspace:evidence.txt#sha256={row['source_sha256']}" in revised.evidence_refs
    assert revised.annotations[-1].evidence_refs == [f"workspace:evidence.txt#sha256={row['source_sha256']}"]


@pytest.mark.parametrize("reference", ["nonexistent:proof", "missing.txt", "../outside.txt", "state:../outside.txt",
                                       "escape.txt", ".env", "large.txt"])
def test_unverified_or_unbounded_evidence_cannot_modify_a_capsule(tmp_path, reference):
    workspace = tmp_path / "workspace"
    service = ExperienceToolService(tmp_path / "state", workspace=workspace, role="engineer", parent_call_id="call")
    item = capsule(service)
    outside = tmp_path / "outside.txt"
    outside.write_text("other project private evidence")
    (workspace / "escape.txt").symlink_to(outside)
    (workspace / ".env").write_text("private credential")
    (workspace / "large.txt").write_text("x" * 32_769)
    with pytest.raises((OSError, ValueError)):
        service.dispatch("revise", {**correction(item.id), "evidence_refs": [reference]})
    assert service.store.get(item.id).revision == 1


@pytest.mark.parametrize("role", ["manager", "planner", "engineer", "reviewer"])
def test_native_role_tools_preserve_workspace_permissions_and_role_boundary(tmp_path, role):
    options = RunnerOptions(sandbox_mode="read-only", working_dir=str(tmp_path))
    ctx = SimpleNamespace(options=options, run_label=role, usage_project_root=tmp_path,
                          backend=SimpleNamespace(_backend_name="pi"), call_id="real-host-call", prompt="role prompt")
    with experience_runtime.experience_run(ctx):
        names = ctx.options.trusted_tool_names
        assert "get_experience" in names and "search_experiences" in names
        assert ("revise_experience" in names) == (role != "reviewer")
        assert ctx.options.sandbox_mode == "read-only"
        assert not ctx.options.dangerous_yolo and ctx.options.force_safe_mode
        assert not ({"bash", "write", "edit"} & set(names))
        assert "expected_revision" in ctx.prompt if role != "reviewer" else "get_experience" in ctx.prompt
    assert options.extension_env is None


def test_actual_gateway_composes_extensions_and_executes_native_experience_tool(tmp_path, monkeypatch):
    from argus.adapters.agent_cli_backend import AgentCliBackend, _core, _exec
    from argus.adapters.agent_cli_backend._exec_finalize import finalize_result
    from argus.advisor.config import save_advisor_config
    from argus.core import secret_guard
    from argus.core.models import RunnerResult

    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable")
    root = tmp_path / "tenant"
    state = root / "projects" / "project-a"
    state.mkdir(parents=True)
    (root / "projects" / "project-b").mkdir()
    save_advisor_config(state, {"enabled": True, "backend": "pi", "model": "independent/expert"})
    monkeypatch.setattr(_core, "known_secret_values", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(secret_guard, "known_secret_values", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(_exec, "monitor_budget", lambda *_args: nullcontext())
    service = ExperienceToolService(state, role="manager", parent_call_id="seed")
    item = capsule(service)
    script = r'''
const {experienceExtension}=await import(process.argv[1]);
const {bridgeRequest}=await import(process.argv[2]);
const Type={Object:properties=>({properties}),String:()=>({}),Integer:()=>({}),Array:()=>({}),Optional:value=>value};
const tools={};
experienceExtension((op,params,signal)=>bridgeRequest("ARGUS_PLUGIN_EXPERIENCE",op,params,signal),Type,true)(
  {registerTool:tool=>{tools[tool.name]=tool;}}
);
const result=await tools.revise_experience.execute("native-tool-call",{
  experience_id:process.argv[3],expected_revision:1,evidence_refs:["state:evidence.txt"],reason:"new controlled observation",
  changes:{factual_outcome:"observed through native role tool"}
});
if(result.isError) throw Error(JSON.stringify(result));
process.stdout.write(JSON.stringify(result.details));
'''
    calls = []

    def provider(ctx, _options):
        calls.append(ctx.call_id)
        assert {"consult_advisor", "send_peer_message", "revise_experience", "get_experience"} <= set(ctx.options.trusted_tool_names)
        assert len(ctx.options.trusted_extensions) == 4
        assert any(path.endswith("skills/runtime_extension.mjs") for path in ctx.options.trusted_extensions)
        command = ctx.backend._runner._build_pi_command(resume_thread_id=None, options=ctx.options)
        names = command[command.index("--tools") + 1].split(",")
        assert "revise_experience" in names and not {"bash", "write", "edit"} & set(names)
        assert ctx.options.sandbox_mode == "read-only" and ctx.options.force_safe_mode
        result = subprocess.run(
            [node, "--input-type=module", "-e", script,
             Path(experience_runtime.EXTENSION).with_name("experience_pi_tools.mjs").as_uri(),
             Path(experience_runtime.__file__).parents[1].joinpath("core/role_tool_bridge.mjs").as_uri(), item.id],
            env={"PATH": os.defpath, **ctx.options.extension_env}, text=True, capture_output=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["revision"] == 2
        return finalize_result(ctx, RunnerResult(exit_code=0, agent_messages=["Corrected the interpretation using the new observation."]), status="completed")

    monkeypatch.setattr(_exec, "spawn_and_finish", provider)
    backend = AgentCliBackend(backend="pi", runner_bin="unused-fake-pi")
    backend.set_usage_context(project_root=state, global_root=root, mission_id="mission-a")
    options = RunnerOptions(sandbox_mode="read-only", working_dir=str(state), model="provider/fake-manager")
    result = backend.run_exec(prompt="Review the latest evidence.", options=options, run_label="manager-chat")
    assert result.exit_code == 0 and len(calls) == 1
    assert options.trusted_extensions is None and options.extension_env is None
    updated = service.store.get(item.id)
    assert updated.factual_outcome == "observed through native role tool"
    assert updated.annotations[-1].relation == f"correction by manager; call:{calls[0]}"


@pytest.mark.parametrize("disable_tools,backend,mode,label", [
    (True, "pi", "read-only", "manager"), (False, "codex", "read-only", "reviewer"),
    (False, "pi", "read-only", "unrecognized"),
])
def test_unavailable_call_does_not_gain_tools(tmp_path, disable_tools, backend, mode, label):
    options = RunnerOptions(disable_tools=disable_tools, sandbox_mode=mode)
    ctx = SimpleNamespace(options=options, run_label=label, usage_project_root=tmp_path,
                          backend=SimpleNamespace(_backend_name=backend), prompt="original")
    with experience_runtime.experience_run(ctx):
        assert ctx.options is options and ctx.prompt == "original"
