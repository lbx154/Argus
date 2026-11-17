import assert from 'node:assert/strict';
import test from 'node:test';
import { claude } from '../src/agent/claude.js';
import type { AgentRequest } from '../src/types.js';
import type { Cli } from '../src/agent/session.js';

const request = { backend: 'claude', role: 'engineer', form: 'engineer', access: 'write', cwd: '/tmp', prompt: '',
  freshAt: 100000, overflow: 'From Argus: this session is at {context} tokens.' } as unknown as AgentRequest;
const step = (id: string, context: number) => ({ type: 'assistant', message: { id, content: [{ type: 'tool_use', name: 'Bash', input: { command: 'true' } }],
  usage: { input_tokens: 10, cache_read_input_tokens: context - 110, cache_creation_input_tokens: 100, output_tokens: 5 } } });

test("Claude's turn is told once, as it goes on, when its context passes the project's limit, and reports the context it ended at", async () => {
  const sent: { type: string; uuid?: string; message: { content: string } }[] = [];
  const cli = {
    send: (message: (typeof sent)[number]) => { sent.push(message); },
    why: () => '',
    // The turn's events, after its prompt is played back; a step's later events repeat its id.
    until: (seen: (event: Record<string, unknown>) => boolean) => new Promise<boolean>(resolve => setImmediate(() => {
      for (const event of [{ type: 'user', uuid: sent[0]!.uuid }, step('m1', 50000), step('m2', 130000), step('m2', 130000), step('m3', 140000),
        { type: 'result', subtype: 'success', is_error: false, result: '', structured_output: { to: 'engineer', letter: 'on' }, usage: {} }]) {
        if (seen(event)) return resolve(true);
      }
      resolve(false);
    })),
  } as unknown as Cli;
  const conversation = claude(request, { confined: true, schema: { type: 'object' }, stem: '/tmp/x' });
  const end = await conversation.turn(cli, 'Go on.', { request, onStep: () => {}, started: Date.now() });
  assert.equal(sent.length, 2);
  assert.equal(sent[1]!.message.content, 'From Argus: this session is at 130000 tokens.');
  assert.equal(end.context, 140000);
});
