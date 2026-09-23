import { readFileSync } from 'node:fs';
import { once } from 'node:events';
import { setTimeout as sleep } from 'node:timers/promises';
const mode = process.argv[2];
let raw = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) raw += chunk;
const request = JSON.parse(raw);
if (mode === 'hang') {
  setInterval(() => {}, 1000);
} else if (mode === 'overflow') {
  process.stdout.write('x'.repeat(17 * 1024 * 1024));
} else {
  const fixtures = JSON.parse(readFileSync(new URL('../../../contracts/fixtures/read-api.json', import.meta.url), 'utf8'));
  fixtures.meta.runtime.source_root = process.cwd();
  let result = fixtures[request.method];
  if (request.method === 'snapshot' && request.params.sid === 'missing') result = null;
  if (mode === 'delayed') await sleep(50);
  const response = { protocol: request.protocol, version: request.version, id: request.id, ok: true, result };
  if (request.method === 'costs') {
    const send = frame => process.stdout.write(JSON.stringify({ protocol: request.protocol, version: request.version, id: request.id, ...frame }) + '\n');
    const summaries = JSON.parse(readFileSync(new URL('../../../runtime/fixtures/usage-summary.json', import.meta.url), 'utf8'));
    const records = mode === 'cost-dedup' ? summaries.find(row => row.id === 'copilot-overlapping-receipts').records
      : summaries.find(row => row.id === 'explicit-zero').records;
    const begin = { kind: 'cost_project', project_id: 's-fixture' };
    if (mode === 'cost-legacy') {
      process.stdout.write(JSON.stringify(response) + '\n');
      process.exit(0);
    }
    if (mode !== 'cost-no-start') send(begin);
    if (mode === 'cost-byte-limit' || mode === 'cost-frame-limit') {
      const frame = JSON.stringify({ protocol: request.protocol, version: request.version, id: request.id,
        kind: 'cost_record', record: records[0], padding: 'x'.repeat(mode === 'cost-frame-limit' ? 2_000_000 : 500_000),
      }) + '\n';
      for (let i = 0; i < 35; i++) {
        if (!process.stdout.write(frame)) await once(process.stdout, 'drain');
      }
    }
    if (mode === 'cost-nested') send(begin);
    for (const record of records) send({ kind: 'cost_record', record: mode === 'cost-invalid' ? { ...record, cost_usd: -1 } : record });
    if (mode === 'cost-no-terminal') process.exit(0);
    if (mode === 'cost-hang') await sleep(60_000);
    if (mode !== 'cost-no-end') send({ kind: 'cost_project_end', project_id: mode === 'cost-wrong-project' ? 'other' : 's-fixture', updated_at: 1 });
    if (mode === 'cost-duplicate') send(begin);
    if (mode === 'cost-wrong-id') send({ ...begin, id: 'different' });
    if (mode === 'cost-wrong-version') send({ ...begin, version: 1 });
    response.result = { project_count: mode === 'cost-wrong-count' ? 2 : 1, generated_at: 2 };
    if (mode === 'cost-error') { response.ok = false; response.error = { code: 'query_failed' }; }
    if (mode === 'cost-nonzero') process.exitCode = 7;
  }
  if (mode === 'wrong-id') response.id = 'another-request';
  if (mode === 'wrong-version') response.version = 100;
  if (mode === 'wrong-root') response.result.runtime.source_root = '/other-installation';
  if (mode === 'error') { response.ok = false; response.error = { code: 'invalid_query', detail: 'rejected' }; }
  process.stdout.write(mode === 'malformed' ? 'not json\n' : JSON.stringify(response) + '\n');
  if (mode === 'extra-output') process.stdout.write('{}\n');
  if (mode === 'nonzero') process.exitCode = 7;
}
