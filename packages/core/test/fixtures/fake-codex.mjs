#!/usr/bin/env node
// Stands in for `codex exec --json` with no model behind it: each turn ends with a letter to the
// one who moves the work on. The Manager hands the request to the Engineer, the Engineer to the
// Reviewer, the Reviewer its judgment to the Manager, who reports to the operator.
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';

const args = process.argv.slice(2);
const request = readFileSync(0, 'utf8');
const { properties } = JSON.parse(readFileSync(args[args.indexOf('--output-schema') + 1], 'utf8'));
// A letter from the team reaches the Manager in a turn of its own.
const to = request.startsWith('From the ') ? 'operator' : ['reviewer', 'manager', 'engineer'].find(place => properties.to.enum.includes(place));
const letter = { engineer: request.trim().split('\n').at(-1), operator: 'The work is done.' }[to] ?? `Ready for the ${to}.`;

const thread = args.includes('resume') ? args.at(-2) : randomUUID();
for (const event of [
  { type: 'thread.started', thread_id: thread },
  { type: 'item.completed', item: { type: 'agent_message', text: JSON.stringify({ to, letter }) } },
  { type: 'turn.completed' },
]) process.stdout.write(`${JSON.stringify(event)}\n`);
