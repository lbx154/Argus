import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const mode = process.argv[2];
let prompt = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) prompt += chunk;
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
if (mode === 'hang') {
  process.on('SIGTERM', () => {});
  send({ type: 'ready', pid: process.pid });
  setInterval(() => {}, 1000);
} else if (mode === 'heartbeat') {
  send({ type: 'ready', pid: process.pid });
  setInterval(() => send({ type: 'heartbeat' }), 20);
} else if (mode === 'line-limit') {
  process.stdout.write('x'.repeat(16_384));
  setInterval(() => {}, 1000);
} else if (mode === 'queue-limit') {
  process.stdout.write(('x'.repeat(64) + '\n').repeat(2000));
  setInterval(() => {}, 1000);
} else if (mode === 'descendant') {
  const descendant = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
  descendant.unref();
  send({ type: 'descendant', pid: descendant.pid });
  send({ type: 'agent_settled' });
} else if (mode === 'inherited-pipe') {
  const descendant = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: ['ignore', 'inherit', 'inherit'] });
  descendant.unref();
  send({ type: 'descendant', pid: descendant.pid });
  send({ type: 'agent_settled' });
} else {
  const text = mode === 'text-limit' ? 'x'.repeat(2048) : `answer: ${prompt}`;
  const events = [
    { type: 'request', args: process.argv.slice(3), prompt },
    { type: 'session', id: 'pi-fixture-session' },
    { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'discarded draft' } },
    { type: 'tool_execution_start', toolName: 'read' },
    { type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text }], stopReason: 'stop' } },
    { type: 'agent_end' },
  ];
  if (mode !== 'incomplete') events.push({ type: 'agent_settled' });
  process.stderr.write('fixture diagnostic\r\n');
  // Deliberately omit a final newline and split inside UTF-8 codepoints.
  const wire = Buffer.from(events.map(value => JSON.stringify(value)).join('\r\n'));
  if (mode === 'chunked') {
    for (let offset = 0; offset < wire.length; offset += 7) {
      process.stdout.write(wire.subarray(offset, offset + 7));
      await sleep(1);
    }
  } else process.stdout.write(wire);
  if (mode === 'nonzero') process.exitCode = 7;
}
