// Models a detached server execution owner. A launcher/client owns no provider.
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { BudgetedPiBackend } from '../dist/index.js';

const path = process.argv[2];
const config = JSON.parse(readFileSync(path, 'utf8'));
const publish = (target, value) => { writeFileSync(target + '.tmp', value); renameSync(target + '.tmp', target); };
if (process.argv[3] === 'launch') {
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), path], { detached: true, stdio: 'ignore' });
  child.unref();
  process.stdout.write(String(child.pid) + '\n');
} else {
  if (process.platform !== 'win32') process.on('SIGHUP', () => {});
  const backend = new BudgetedPiBackend({
    python: { executable: config.python, sourceRoot: config.sourceRoot, globalRoot: config.root },
    pi: { executable: process.execPath, prefixArgs: [fileURLToPath(new URL('./guarded-provider.mjs', import.meta.url))],
      env: { ...process.env, ARGUS_TEST_TREE_MODE: config.mode, ARGUS_TEST_TREE_PIDS: config.pids,
        ARGUS_TEST_TREE_RELEASE: config.release }, terminateGraceMs: 300 },
  });
  for await (const event of backend.run({ projectId: 'p', model: 'gpt-5.6-sol', provider: 'openai',
    cwd: config.root, prompt: 'Offline process-ownership fixture', toolPolicy: 'disabled',
    wallTimeoutMs: 30_000, idleTimeoutMs: 20_000,
  })) {
    if (event.type === 'provider_event' && event.event.type === 'guard_test_ready') publish(config.ready, 'ready');
    if (event.type === 'result') publish(config.result, JSON.stringify(event.result));
  }
}
