// Opt-in real inference: a bounded, isolated four-role acceptance task.
// Run explicitly with ARGUS_REAL_CLI_SMOKE=1; never part of npm test/build.
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { Store } from '../../packages/core/src/store.ts';
import { Flow } from '../../packages/core/src/flow.ts';
import { applyRoleRuntimes } from '../../packages/core/src/roleRuntime.ts';
import { callAgent } from '../../packages/core/src/agent/call.ts';

if (process.env.ARGUS_REAL_CLI_SMOKE !== '1') throw new Error('Real inference requires explicit ARGUS_REAL_CLI_SMOKE=1.');
const root = resolve('..');
mkdirSync(join(root, '.preview-qa'), { recursive: true });
const resume = process.env.ARGUS_SMOKE_RESUME;
const home = resume ? resolve(resume) : mkdtempSync(join(root, '.preview-qa', 'mixed-cli-'));
if (!home.startsWith(join(root, '.preview-qa') + '\\')) throw new Error('Only this worktree’s isolated QA projects may be resumed.');
process.env.ARGUS_HOME = home;
const store = new Store(home);
const project = resume ? store.list().at(-1) : store.create({ workdir: '', request: '', backend: 'pi', model: null });
if (!project) throw new Error('No QA project to resume.');
if (!resume) applyRoleRuntimes(project, {
  manager: { backend: 'pi', model: 'github-copilot/gpt-6.1-sol', effort: 'low' },
  planner: { backend: 'pi', model: 'github-copilot/gpt-6-astra', effort: 'low' },
  engineer: { backend: 'dsh', model: 'deepseek-official/deepseek-flash' },
  reviewer: { backend: 'pi', model: 'github-copilot/gpt-6.1-sol', effort: 'low' },
});
if (!resume) store.save(project);
const controller = new AbortController();
const deadline = setTimeout(() => controller.abort(), 240_000);
const calls = resume ? JSON.parse(readFileSync(join(home, 'result.json'), 'utf8')).calls : [];
const safe = value => String(value ?? '').replace(/(Bearer\s+|(?:api[_-]?key|password|secret|token)\s*[:=]\s*)\S+/gi, '$1***').replace(/sk-[A-Za-z0-9_-]+/g, '***').slice(0, 500);
const call = Object.assign(async request => {
  if (calls.length >= 10) { controller.abort(); throw new Error('Smoke call budget exceeded.'); }
  const row = { role: request.role, backend: request.backend, requestedModel: request.model, completed: false };
  calls.push(row);
  console.log(`SMOKE_CALL=${row.role}/${row.backend}/${row.requestedModel}`);
  const result = await callAgent(request);
  Object.assign(row, { completed: result.completed, actualModel: result.model, handoff: result.handoff?.to ?? null,
    durationMs: result.ms, costUsd: result.usage?.cost_usd ?? null, error: safe(result.error) });
  return result;
}, { close: callAgent.close, steer: callAgent.steer });
const flow = new Flow(store, call, { signal: controller.signal });
try {
  if (resume) flow.resume(project.id);
  else await flow.say(project.id, 'This is an explicitly authorized, very small integration test. Manager: hand this task to the Planner using hand_off to=planner. Planner: give ONE Engineer task to create alpha.txt containing exactly alpha and beta.txt containing exactly beta (optional trailing newlines). Engineer: create only these two files in the current workspace, then hand them to the Reviewer. Reviewer: read both files and send acceptance to the Planner. Planner: report completion to Manager. Manager: report both files to the operator. Do not install anything, browse the web, read credentials, or touch files outside this workspace. No additional work is needed.');
  const result = await flow.settled(project.id);
  const report = { status: result.status, calls, workspace: project.workdir, errors: result.failures?.map(failure => ({ role: failure.role, error: safe(failure.error) })) ?? [] };
  writeFileSync(join(home, resume ? `result-resume-${Date.now()}.json` : 'result.json'), JSON.stringify(report, null, 2), { flag: 'wx' });
  console.log(JSON.stringify(report, null, 2));
  assert.equal(result.status, 'idle');
  assert.equal(readFileSync(join(project.workdir, 'alpha.txt'), 'utf8').trim(), 'alpha');
  assert.equal(readFileSync(join(project.workdir, 'beta.txt'), 'utf8').trim(), 'beta');
  for (const role of ['manager', 'planner', 'engineer', 'reviewer']) assert.ok(calls.some(call => call.role === role && call.completed), role);
  console.log(`MIXED_CLI_ACCEPTANCE_PASSED=${home}`);
} finally { clearTimeout(deadline); controller.abort(); await callAgent.close?.(`${project.id}/`); }
