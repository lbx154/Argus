// Protocol fixture only. Actual OS lifetime guarantees are tested with Python.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
const input = createInterface({ input: process.stdin });
const mode = process.argv[2];
let child;
let config;
const send = frame => process.stdout.write(JSON.stringify({ protocol: config.protocol, version: config.version, ...frame }) + '\n');
for await (const line of input) {
  if (config) process.exit(1);
  config = JSON.parse(line);
  if (mode === 'no-receipt') process.exit(0);
  if (mode === 'wrong-version') { send({ type: 'exit', code: 0, signal: null, error: null, version: 999 }); process.exit(0); }
  if (mode === 'unterminated') { process.stdout.write(JSON.stringify({ protocol: config.protocol, version: config.version, type: 'exit', code: 0, signal: null, error: null })); process.exit(0); }
  if (mode === 'oversized') { process.stdout.write('x'.repeat(100_000)); process.exit(0); }
  child = spawn(config.command[0], config.command.slice(1), { cwd: config.cwd, env: config.env, stdio: ['pipe', 'pipe', 'pipe'] });
  for (const stream of ['stdout', 'stderr']) child[stream].on('data', chunk => {
    for (let start = 0; start < chunk.length; start += 8192) send({ type: 'data', stream, data: chunk.subarray(start, start + 8192).toString('base64') });
  });
  child.stdin.end(config.input);
  child.on('close', (code, signal) => { send({ type: 'exit', code, signal, error: null }); process.exit(0); });
}
if (child) {
  child.kill('SIGKILL');
} else process.exit(0);
