#!/usr/bin/env node
// Stands in for `codex app-server` with no model behind it: each turn ends with a letter to the
// one who moves the work on. The Manager hands the request to the Engineer, the Engineer to the
// Reviewer, the Reviewer its judgment to the Manager, who reports to the operator.
import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';

const emit = message => process.stdout.write(`${JSON.stringify(message)}\n`);
let thread = null;
createInterface({ input: process.stdin }).on('line', line => {
  const { id, method, params } = JSON.parse(line);
  if (method === 'initialize') return emit({ id, result: {} });
  if (method === 'thread/start' || method === 'thread/resume') {
    thread = params.threadId ?? randomUUID();
    return emit({ id, result: { thread: { id: thread }, model: 'fake' } });
  }
  if (method !== 'turn/start') return;
  emit({ id, result: { turn: { id: 'turn' } } });
  const request = params.input[0].text;
  const { properties } = params.outputSchema;
  // A letter from the team reaches the Manager in a turn of its own.
  const to = request.startsWith('From the ') ? 'operator' : ['reviewer', 'manager', 'engineer'].find(place => properties.to.enum.includes(place));
  const letter = { engineer: request.trim().split('\n').at(-1), operator: 'The work is done.' }[to] ?? `Ready for the ${to}.`;
  for (const [method, more] of [
    ['item/completed', { item: { type: 'agentMessage', text: JSON.stringify({ to, letter, open: [], ...properties.task ? { task: '' } : {} }) } }],
    ['turn/completed', { turn: { id: 'turn', status: 'completed', error: null } }],
  ]) emit({ method, params: { threadId: thread, ...more } });
});
