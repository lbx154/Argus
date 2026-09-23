// Offline integration entrypoint: real Node runtime and Python owner, fixture Pi.
import { BudgetedPiBackend } from '../dist/index.js';
import { fileURLToPath } from 'node:url';
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const options = JSON.parse(raw);
const controller = new AbortController();
const backend = new BudgetedPiBackend({
  python: { executable: options.python, sourceRoot: options.sourceRoot, globalRoot: options.globalRoot, timeoutMs: 10_000 },
  pi: { executable: process.execPath,
    prefixArgs: [fileURLToPath(new URL('../test/fixtures/fake-pi.mjs', import.meta.url)), options.mode], terminateGraceMs: 100 },
});
let events = 0;
for await (const event of backend.run({
  projectId: 'p', model: 'gpt-5.6-sol', provider: 'openai', cwd: options.globalRoot,
  prompt: 'Offline budget integration fixture', toolPolicy: 'disabled',
  wallTimeoutMs: 5000, idleTimeoutMs: 2000, signal: controller.signal,
  ...options.request,
})) {
  if (event.type === 'provider_event') {
    events += 1;
    if (options.cancel && event.event.type === 'ready') controller.abort();
  }
  if (event.type === 'result') process.stdout.write(JSON.stringify({ ...event.result, events }) + '\n');
}
