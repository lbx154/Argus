import assert from 'node:assert/strict';
import test from 'node:test';
import { MessageStream } from '../src/agent/streaming.js';

const pi = (type: string, fields: object = {}) => ({ type: 'message_update', assistantMessageEvent: { type, contentIndex: 0, ...fields } });

test('Pi text is visible incrementally without cumulative SDK snapshots', () => {
  const words: string[] = []; const stream = new MessageStream(text => words.push(text));
  stream.pi(pi('text_start'));
  stream.pi(pi('text_delta', { delta: 'Hello' }));
  assert.equal(words.at(-1), 'Hello');
  stream.pi(pi('text_delta', { delta: ' world' }));
  assert.equal(words.at(-1), 'Hello world');
  stream.pi(pi('text_end', { content: 'Authoritative text' }));
  assert.equal(words.at(-1), 'Authoritative text');
});

test('Pi hand_off streams only the letter, never arbitrary tool arguments or reasoning', () => {
  const words: string[] = []; const stream = new MessageStream(text => words.push(text));
  stream.pi(pi('thinking_delta', { delta: 'not visible' }));
  stream.pi(pi('toolcall_start', { toolName: 'bash' }));
  stream.pi(pi('toolcall_delta', { delta: '{"command":"never show arguments"}' }));
  assert.equal(words.length, 0);
  stream.pi(pi('toolcall_start', { toolName: 'hand_off' }));
  stream.pi(pi('toolcall_delta', { delta: '{"to":"operator","letter":"Hi' }));
  assert.equal(words.at(-1), 'Hi');
  stream.pi(pi('toolcall_delta', { delta: '\\u4e' }));
  assert.equal(words.at(-1), 'Hi');
  stream.pi(pi('toolcall_delta', { delta: '2d"}' }));
  assert.equal(words.at(-1), 'Hi中');
  assert.ok(words.every(value => !value.includes('operator')));
});

test('Claude incremental text ignores subagent and thinking events', () => {
  const words: string[] = []; const stream = new MessageStream(text => words.push(text));
  const delta = (text: string, extra = {}) => ({ type: 'stream_event', ...extra, event: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text } } });
  stream.claude(delta('excluded', { parent_tool_use_id: 'sub' })); assert.equal(words.length, 0);
  stream.claude(delta('first')); assert.equal(words.at(-1), 'first');
  stream.claude(delta(' second')); assert.equal(words.at(-1), 'first second');
  stream.clear(); assert.equal(words.at(-1), '');
});

test('incomplete JSON is display-only, bounded, and reset for retries', () => {
  const words: string[] = []; const stream = new MessageStream(text => words.push(text));
  stream.text(0, '{"to":"manager",'); assert.equal(words.at(-1), '');
  stream.text(0, '"letter":"Partial'); assert.equal(words.at(-1), 'Partial');
  stream.clear(); stream.text(0, 'next'); assert.equal(words.at(-1), 'next');
  stream.text(0, 'x'.repeat(100_000), true); assert.equal(words.at(-1)?.length, 65_536);
  stream.text(-1, 'bad'); assert.equal(words.at(-1)?.length, 65_536);
});
