import { readFileSync } from 'node:fs';
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
  if (mode === 'wrong-id') response.id = 'another-request';
  if (mode === 'wrong-version') response.version = 100;
  if (mode === 'wrong-root') response.result.runtime.source_root = '/other-installation';
  if (mode === 'error') { response.ok = false; response.error = { code: 'invalid_query', detail: 'rejected' }; }
  process.stdout.write(mode === 'malformed' ? 'not json\n' : JSON.stringify(response) + '\n');
  if (mode === 'extra-output') process.stdout.write('{}\n');
  if (mode === 'nonzero') process.exitCode = 7;
}
