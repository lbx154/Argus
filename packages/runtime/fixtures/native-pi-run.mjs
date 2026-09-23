// Explicit native-CLI test entrypoint; callers supply isolated agent config.
import { PiBackend } from '../dist/index.js';
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const options = JSON.parse(raw);
const runner = new PiBackend({ executable: options.executable, prefixArgs: options.prefixArgs ?? [],
  guardian: { executable: options.python, sourceRoot: options.sourceRoot }, terminateGraceMs: 1000 });
for await (const event of runner.run({ cwd: options.cwd, prompt: 'Return the local fixture answer.',
  model: options.model, toolPolicy: 'disabled', wallTimeoutMs: 20_000, idleTimeoutMs: 10_000, ...options.request })) {
  if (event.type === 'result') process.stdout.write(JSON.stringify(event.result) + '\n');
}
