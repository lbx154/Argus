// Opt-in native provider tests for all CLI installations available on this machine.
// These two additional routes exercise Codex, Pi and DSH in different roles/directions.
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { Store } from '../../packages/core/src/store.ts';
import { Flow } from '../../packages/core/src/flow.ts';
import { applyRoleRuntimes } from '../../packages/core/src/roleRuntime.ts';
import { callAgent } from '../../packages/core/src/agent/call.ts';
import { models } from '../../packages/core/src/agent/models.ts';

if (process.env.ARGUS_REAL_CLI_SMOKE !== '1') throw new Error('Real inference requires explicit opt-in.');
const root = resolve('..');
const parent = join(root, '.preview-qa'); mkdirSync(parent, { recursive: true });
const codexModels = await models('codex', root);
const codexModel = codexModels.find(model => model.default)?.model;
if (!codexModel) throw new Error('Codex did not report a default model; native tests must not guess one.');
const routes = {
  codex: { backend: 'codex', model: codexModel, effort: 'low' },
  pi: { backend: 'pi', model: 'github-copilot/gpt-6.1-sol', effort: 'low' },
  dsh: { backend: 'dsh', model: 'deepseek-official/deepseek-flash' },
  claude: { backend: 'claude', model: 'sonnet', effort: 'low' },
};
const cases = [['codex', 'pi', 'dsh'], ['dsh', 'codex', 'pi'], ['pi', 'claude', 'dsh'], ['claude', 'pi', 'codex'], ['dsh', 'pi', 'dsh']].filter(route => !process.env.ARGUS_NATIVE_CASE || route.join(',') === process.env.ARGUS_NATIVE_CASE);
const reports = [];
for (const [manager, engineer, reviewer] of cases) {
  const home = mkdtempSync(join(parent, `native-${manager}-${engineer}-${reviewer}-`));
  process.env.ARGUS_HOME = home;
  const store = new Store(home);
  const project = store.create({ workdir: '', request: '', backend: manager, model: routes[manager].model });
  applyRoleRuntimes(project, { manager: routes[manager], planner: routes.pi, engineer: routes[engineer], reviewer: routes[reviewer] });
  store.save(project);
  const controller = new AbortController();
  const timeoutMs = Number(process.env.ARGUS_SMOKE_TIMEOUT_MS ?? 180_000);
  if (!Number.isFinite(timeoutMs) || timeoutMs < 5_000 || timeoutMs > 600_000) throw new Error('Smoke timeout must be 5000–600000 ms.');
  const deadline = setTimeout(() => controller.abort(), timeoutMs);
  const calls = [];
  const call = Object.assign(async request => {
    if (calls.length >= 6) { controller.abort(); throw new Error('Native smoke call budget exceeded.'); }
    const row = { role: request.role, backend: request.backend, requestedModel: request.model }; calls.push(row);
    console.log(`NATIVE_CLI_CALL=${row.role}/${row.backend}/${row.requestedModel}`);
    const result = await callAgent(request);
    Object.assign(row, { completed: result.completed, actualModel: result.model, recipient: result.handoff?.to ?? null,
      resumed: !!request.resume, durationMs: result.ms, costUsd: result.usage?.cost_usd ?? null,
      error: String(result.error ?? '').replace(/(Bearer\s+|(?:api[_-]?key|password|secret|token)\s*[:=]\s*)\S+/gi, '$1***').slice(0, 500) });
    return result;
  }, { close: callAgent.close, steer: callAgent.steer });
  const flow = new Flow(store, call, { signal: controller.signal });
  try {
    const content = `${manager}-${engineer}-${reviewer}-ok`;
    await flow.say(project.id, `This is an authorized tiny CLI integration test, not a planning task. Manager: give one task directly to Engineer (not Planner). Engineer: create only cli-check.txt in the current workspace, containing exactly ${content} (optional final newline), then hand off to Reviewer. Reviewer: read that file and report acceptance to Manager. Manager: report the file to operator. Do not install packages, use network tools, read credentials, or touch any other directory. Use hand_off if that tool exists; otherwise return the requested JSON hand-off form.`);
    const ended = await flow.settled(project.id);
    let fileMatches = false;
    try { fileMatches = readFileSync(join(project.workdir, 'cli-check.txt'), 'utf8').trim() === content; } catch {}
    const ok = ended.status === 'idle' && fileMatches && [manager, engineer, reviewer].every(backend => calls.some(call => call.backend === backend && call.completed));
    const report = { route: { manager, engineer, reviewer }, ok, status: ended.status, fileMatches, calls, evidence: home };
    writeFileSync(join(home, 'result.json'), JSON.stringify(report, null, 2), { flag: 'wx' }); reports.push(report);
  } finally { clearTimeout(deadline); controller.abort(); await callAgent.close?.(`${project.id}/`); }
}
console.log(JSON.stringify(reports, null, 2));
assert.ok(reports.every(report => report.ok), 'One native CLI combination failed; do not mark it verified.');
console.log(`NATIVE_CLI_COMBINATIONS_PASSED=${reports.length}`);
