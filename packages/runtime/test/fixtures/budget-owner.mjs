import { createInterface } from 'node:readline';
import { appendFileSync } from 'node:fs';
const [mode, trace] = process.argv.slice(2);
if (process.argv.includes('argus.agent_cli.process_guard')) {
  await import('./fake-process-guard.mjs');
} else {
for await (const line of createInterface({ input: process.stdin })) {
  const query = JSON.parse(line);
  appendFileSync(trace, JSON.stringify(query) + '\n');
  if (mode === 'hang') { await new Promise(() => {}); }
  if (mode === 'owner-dies' && query.method === 'observe') process.exit(1);
  if (mode === 'owner-dies-after-usage' && query.method === 'observe' && query.params.accounting.pricing.cost_usd > 0) process.exit(1);
  let result = {};
  if (query.method === 'reserve') result = { admitted: mode !== 'denied', call_id: 'fixture-call',
    reason: mode === 'denied' ? 'global daily budget exhausted' : '', source_root: process.cwd(), global_root: process.cwd() };
  if (query.method === 'start') result = { started: true };
  if (query.method === 'observe') result = { stop_reason: mode === 'cap' && query.params.accounting.pricing.cost_usd > 0 ? 'global daily budget exhausted' : '' };
  if (query.method === 'settle') result = { settlement: query.params.completed ? 'settled' : 'unresolved', call_id: 'fixture-call' };
  if (query.method === 'release') result = { released: true };
  const response = { protocol: query.protocol, version: query.version, id: query.id, ok: true, result };
  if (mode === 'wrong-version') response.version = 999;
  process.stdout.write(JSON.stringify(response) + '\n');
}
}
