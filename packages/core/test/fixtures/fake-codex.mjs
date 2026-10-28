#!/usr/bin/env node
// Stands in for `codex exec --json` with no model behind it: each turn ends with a letter to the
// place that moves the work on. The Manager hands the request to the Engineer, the Engineer
// hands it to review, the Reviewer accepts.
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';

const args = process.argv.slice(2);
const request = readFileSync(0, 'utf8');
const { properties } = JSON.parse(readFileSync(args[args.indexOf('--output-schema') + 1], 'utf8'));
const to = ['reviewer', 'done', 'engineer'].find(place => properties.to.enum.includes(place));
const letter = to === 'engineer' ? request.trim().split('\n').at(-1) : `Ready for ${to}.`;

const thread = args.includes('resume') ? args.at(-2) : randomUUID();
for (const event of [
  { type: 'thread.started', thread_id: thread },
  { type: 'item.completed', item: { type: 'agent_message', text: JSON.stringify({ to, letter }) } },
  { type: 'turn.completed' },
]) process.stdout.write(`${JSON.stringify(event)}\n`);
