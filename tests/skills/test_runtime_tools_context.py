import json
import os
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.adapters.agent_cli_backend import AgentCliBackend, _exec
from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.core.role_tool_bridge import bridge_request
from argus_skill.skills import runtime_tools_context as runtime
from argus_skill.skills.role_library import render_skill_library_paths
from argus_skill.skills.store import SkillStore
from argus_skill.wiki.context import render_knowledge_wiki_block


@pytest.mark.parametrize('label,backend,disabled,isolated', [
    ('advisor.engineer', 'pi', False, False),
    ('supervisor-1', 'pi', False, False),
    ('engineer-r1', 'codex', False, False),
    ('engineer-r1', 'pi', True, False),
    ('self-implement', 'pi', False, True),
])
def test_non_task_calls_do_not_receive_evolution_capabilities(tmp_path, label, backend, disabled, isolated):
    options = RunnerOptions(working_dir=str(tmp_path), disable_tools=disabled, isolate_workdir=isolated)
    ctx = SimpleNamespace(options=options, run_label=label, usage_project_root=tmp_path,
                          backend=SimpleNamespace(_backend_name=backend), prompt='unchanged')
    with runtime.runtime_tools_run(ctx):
        assert ctx.options is options and ctx.prompt == 'unchanged'


@pytest.mark.parametrize('label,read_only,enabled,can_publish', [
    ('engineer-r1', False, True, True), ('simple-1', False, True, True),
    ('main', False, True, True), ('self-micro', False, True, True),
    ('manager.turn', True, True, False), ('reviewer', False, True, False),
    ('engineer-r1', False, False, False),
])
def test_context_preserves_tools_and_memory_switch(tmp_path, monkeypatch, label, read_only, enabled, can_publish):
    monkeypatch.setenv('ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING', '1' if enabled else '0')
    options = RunnerOptions(working_dir=str(tmp_path), sandbox_mode='read-only' if read_only else None,
                           trusted_extensions=['existing.mjs'], trusted_tool_names=['existing_tool'])
    ctx = SimpleNamespace(options=options, run_label=label, usage_project_root=tmp_path,
                          backend=SimpleNamespace(_backend_name='pi'), prompt='task', call_id='parent')
    with runtime.runtime_tools_run(ctx):
        assert ctx.options.trusted_extensions == ['existing.mjs', runtime.EXTENSION]
        assert 'existing_tool' in ctx.options.trusted_tool_names
        assert ('evolve_runtime' in ctx.options.trusted_tool_names) is can_publish
        assert ctx.options.sandbox_mode == options.sandbox_mode
        assert 'same role turn and budget' in ctx.prompt
        env = dict(ctx.options.extension_env)
        assert bridge_request('ARGUS_PLUGIN_RUNTIME', 'list', {}, env=env) == {'tools': []}
    assert options.trusted_tool_names == ['existing_tool']
    with pytest.raises(OSError):
        bridge_request('ARGUS_PLUGIN_RUNTIME', 'list', {}, env=env)


def test_normal_backend_pi_tools_publish_then_reuse_without_a_second_agent(tmp_path, monkeypatch):
    """Real backend admission/context + native tool handlers + workers; fake only the model."""
    from argus_skill.adapters.agent_cli_backend._exec_finalize import finalize_result

    monkeypatch.setenv('ARGUS_SKILL_COST_CONTROL', 'off')
    monkeypatch.setenv('ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING', '1')
    monkeypatch.setattr(_exec, 'monitor_budget', lambda *_args: nullcontext())
    state, workspace = tmp_path / 'state', tmp_path / 'workspace'
    workspace.mkdir()
    observed_calls = []
    node = shutil.which('node')
    assert node
    script = tmp_path / 'native-tools.mjs'
    script.write_text('''
import {runtimeExtension} from "'''+ Path(runtime.EXTENSION).with_name('runtime_pi_tools.mjs').as_uri() + '''";
import {bridgeRequest} from "'''+ (Path(runtime.EXTENSION).parent.parent / 'core/role_tool_bridge.mjs').as_uri() + '''";
const Type = {Object: (properties, opts={}) => ({type:"object", properties, ...opts}),
  String: () => ({type:"string"}), Integer: opts => ({type:"integer", ...opts}),
  Unknown: () => ({}), Array: (items, opts={}) => ({type:"array", items, ...opts})};
const tools = {}, hooks = {};
runtimeExtension((op, value, signal) => bridgeRequest("ARGUS_PLUGIN_RUNTIME", op, value, signal), Type, true)({
  registerTool: tool => {tools[tool.name] = tool;}, on: (name, handler) => {hooks[name] = handler;}
});
const signal = new AbortController().signal;
async function invoke(name, args) {
  const result = await tools[name].execute(name, args, signal);
  if (result.isError) throw Error(JSON.stringify(result));
  return result.details;
}
if (process.env.TEST_FIRST_TURN === "1") {
  for (const id of ["one", "two", "three"]) {
    const event = await hooks.tool_result({toolCallId:id+"|signed", toolName:"read", content:[{type:"text",text:"IDs are strings"}]});
    if (!event.content.at(-1).text.includes(id)) throw Error("missing observation");
  }
  await invoke("evolve_runtime", {name:"normalize-identifiers", expected_revision:0,
    source:"def run(value):\\n    return [item.zfill(4) for item in value]\\n",
    cases:[{input:[],expected:[]},{input:["7"],expected:["0007"]}], evidence:["one","two"],
    skill:{name:"Normalize identifiers",description:"Normalize textual IDs with zero padding.",content:"Call run_learned_tool with normalize-identifiers for string IDs; avoid arithmetic fields."},
    wiki:{title:"Identifier tool contract",description:"String inputs and preservation boundaries.",content:"Observations one and two establish textual source IDs. Empty arrays and single digit values are covered; no numeric coercion is supported."}
  });
}
const used = await invoke("run_learned_tool", {name:"normalize-identifiers",input:["8"]});
if (JSON.stringify(used.output) !== '["0008"]') throw Error("wrong tool result");
process.stdout.write(JSON.stringify(used));
''')

    def provider(ctx, cli_options):
        observed_calls.append(ctx.call_id)
        assert runtime.EXTENSION in cli_options.trusted_extensions
        command = ctx.backend._runner._build_pi_command(resume_thread_id=None, options=cli_options)
        assert runtime.EXTENSION in command
        assert 'evolve_runtime' in ctx.options.trusted_tool_names
        environment = {'PATH': os.defpath, **ctx.options.extension_env,
                       'TEST_FIRST_TURN': '1' if len(observed_calls) == 1 else '0'}
        result = subprocess.run([node, str(script)], env=environment, capture_output=True,
                                text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt['published'] is (len(observed_calls) == 1)
        return finalize_result(ctx, RunnerResult(exit_code=0, agent_messages=['Task output verified.']), status='completed')

    monkeypatch.setattr(_exec, 'spawn_and_finish', provider)
    backend = AgentCliBackend(backend='pi', runner_bin='unused-local-model-fixture')
    backend.set_usage_context(project_root=state, global_root=tmp_path / 'global', mission_id='mission')
    for _ in range(2):
        result = backend.run_exec(prompt='Normalize these string identifiers.',
                                  options=RunnerOptions(working_dir=str(workspace)), run_label='engineer-r1')
        assert result.exit_code == 0
    assert len(observed_calls) == 2  # Exactly the two user turns; no review/evolution model calls.
    recalled = render_skill_library_paths(SkillStore(state / 'skills'), role='engineer', task='normalize identifiers')
    assert 'run_learned_tool' in recalled and 'normalize-identifiers' in recalled
    assert '.autors/runtime/wiki' in render_knowledge_wiki_block(state, role='Engineer')
