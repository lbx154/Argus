#!/usr/bin/env node
// A step's rollouts at a glance: node rollouts.mjs <file.jsonl>, one JSON object a line with
// `response` (or `completion`), `reward`, and if known `finish_reason` (or `truncated`) and `group`.
import { readFileSync } from 'node:fs';

const rows = readFileSync(process.argv[2], 'utf8').split('\n').filter(line => line.trim()).map(line => JSON.parse(line));
const share = count => `${(100 * count / rows.length).toFixed(1)}%`;
const mean = values => values.reduce((sum, value) => sum + value, 0) / (values.length || 1);
const rewards = rows.map(row => Number(row.reward));
const cut = rows.filter(row => row.truncated === true || row.finish_reason === 'length').length;
const groups = Map.groupBy(rows.filter(row => row.group !== undefined), row => row.group);
const tied = [...groups.values()].filter(group => new Set(group.map(row => row.reward)).size === 1).length;

console.log(`rollouts: ${rows.length}`);
console.log(`cut off at the length cap: ${cut} (${share(cut)})`);
console.log(`mean reward: ${mean(rewards).toFixed(4)}`);
if (groups.size) console.log(`groups whose rewards all tie: ${tied} of ${groups.size}`);
console.log(`mean response length: ${Math.round(mean(rows.map(row => String(row.response ?? row.completion ?? '').length)))} characters`);
