#!/usr/bin/env node
// Can this data teach this model? Give it the starting model's rollouts as JSONL, a response a
// line with its prompt's id (`id`, `prompt_id` or `question`), its `reward` and, if known,
// `cap_hit`. Only the prompts the model sometimes solves carry a GRPO signal.
import { readFileSync } from 'node:fs';

const rows = readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
const groups = Map.groupBy(rows, row => row.id ?? row.prompt_id ?? row.question);
let right = 0, wrong = 0;
for (const group of groups.values()) {
  const solved = group.filter(row => Number(row.reward) > 0).length;
  if (solved === group.length) right++;
  else if (solved === 0) wrong++;
}
const mixed = groups.size - right - wrong;
const share = n => `${Math.round(100 * n / groups.size)}%`;
console.log(`${groups.size} prompts, ${(rows.length / groups.size).toFixed(1)} responses each`);
console.log(`always right ${share(right)}, always wrong ${share(wrong)}, sometimes right ${share(mixed)}`);
console.log(`cut off at the length cap: ${(100 * rows.filter(row => row.cap_hit).length / rows.length).toFixed(1)}% of responses`);
if (mixed < groups.size / 2) console.log('Most prompts teach nothing: change the data, harder or filtered by this pass rate, before anything else.');
