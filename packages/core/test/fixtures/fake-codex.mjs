#!/usr/bin/env node
// Stands in for `codex exec --json` with no model behind it: each turn ends with the form its
// --output-schema holds it to, filled so the work moves on. The Manager files the request for
// the team, the Engineer hands it to review, the Reviewer accepts; an optional field (nullable
// in the strict schema) is left out.
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';

const args = process.argv.slice(2);
const request = readFileSync(0, 'utf8');
const { properties } = JSON.parse(readFileSync(args[args.indexOf('--output-schema') + 1], 'utf8'));
const to = ['team', 'reviewer', 'done'].find(place => properties.to.enum.includes(place));
const form = Object.fromEntries(Object.entries(properties).map(([name, field]) => [name,
  [field.type].flat().includes('null') || field.enum?.includes(null) ? null : field.type === 'array' ? [] : `${name} for ${to}`]));
Object.assign(form, { to }, to === 'team' && { goal: request.trim().split('\n').at(-1), acceptance: ['The request is met.'] });

const thread = args.includes('resume') ? args.at(-2) : randomUUID();
for (const event of [
  { type: 'thread.started', thread_id: thread },
  { type: 'item.completed', item: { type: 'agent_message', text: JSON.stringify(form) } },
  { type: 'turn.completed' },
]) process.stdout.write(`${JSON.stringify(event)}\n`);
