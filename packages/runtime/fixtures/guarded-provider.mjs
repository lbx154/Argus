// Offline process tree with externally controlled completion; no provider access.
import { appendFileSync, existsSync, readFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';

const role = ['child', 'grandchild'].includes(process.argv[2]) ? process.argv[2] : 'provider';
const mode = process.env.ARGUS_TEST_TREE_MODE;
const identityFile = process.env.ARGUS_TEST_TREE_PIDS;
appendFileSync(identityFile, JSON.stringify({ role, pid: process.pid }) + '\n');
process.on('SIGTERM', () => {});
const launch = childRole => {
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), childRole], {
    stdio: mode === 'inherited-pipe' ? ['ignore', 'inherit', 'inherit'] : 'ignore',
  });
  child.unref();
};
if (role === 'child') launch('grandchild');
if (role !== 'provider') setInterval(() => {}, 1000);
else {
  for await (const _ of process.stdin) { /* consume the finite prompt */ }
  launch('child');
  while (readFileSync(identityFile, 'utf8').trim().split('\n').length < 3) await sleep(10);
  const send = value => process.stdout.write(JSON.stringify(value) + '\n');
  send({ type: 'message_end', message: { role: 'assistant', model: 'gpt-5.6-sol', provider: 'openai',
    stopReason: 'stop', usage: { input: 20, output: 5, cost: { total: 0.1 } } } });
  send({ type: 'guard_test_ready' });
  if (!['root-exit', 'inherited-pipe'].includes(mode)) {
    while (!existsSync(process.env.ARGUS_TEST_TREE_RELEASE)) await sleep(10);
  }
  process.stdout.write(JSON.stringify({ type: 'agent_settled' }) + '\n', () => process.exit(0));
}
