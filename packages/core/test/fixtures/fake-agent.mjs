#!/usr/bin/env node
// Stands in for `codex exec --json` and `claude -p --output-format stream-json`. It reads a
// scenario as its prompt and ends the turn with the scenario's output as its final answer, as
// the real CLI does under --output-schema or --json-schema. Beside the workdir it notes the
// schema it was held to and the credentials it could see.
import { spawn } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const args = process.argv.slice(2);
const codex = args[0] === 'exec';
const scenario = JSON.parse(readFileSync(0, 'utf8'));
const emit = event => process.stdout.write(`${JSON.stringify(event)}\n`);
const option = name => args[args.indexOf(name) + 1];

const schema = JSON.parse(codex ? readFileSync(option('--output-schema'), 'utf8') : option('--json-schema'));
writeFileSync(join(process.cwd(), `seen-${codex ? 'codex' : 'claude'}.json`), JSON.stringify({
  schema, env: Object.keys(process.env).filter(name => /^(GH_TOKEN|MY_SECRET|ANTHROPIC_API_KEY)$/.test(name)),
}));

const uuid7 = () => {
  const hex = Date.now().toString(16).padStart(12, '0') + randomBytes(10).toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-7${hex.slice(13, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
};
const thread = codex ? (args.includes('resume') ? args.at(-2) : uuid7()) : (args.includes('--resume') ? option('--resume') : randomUUID());
if (codex) emit({ type: 'thread.started', thread_id: thread });
else emit({ type: 'system', subtype: 'init', session_id: thread, model: 'claude-test' });

if (scenario.hang) {
  spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)', scenario.hang], { stdio: 'ignore' });
  setInterval(() => {}, 1000);
} else {
  const output = scenario.output;
  const answer = typeof output === 'string' ? output : JSON.stringify(output);
  if (codex) {
    const began = new Date(parseInt(thread.replace(/-/g, '').slice(0, 12), 16));
    const pad = value => String(value).padStart(2, '0');
    const day = join(process.env.CODEX_HOME || join(homedir(), '.codex'), 'sessions',
      String(began.getFullYear()), pad(began.getMonth() + 1), pad(began.getDate()));
    mkdirSync(day, { recursive: true });
    const timestamp = new Date().toISOString();
    appendFileSync(join(day, `rollout-${began.toISOString().slice(0, 19).replace(/:/g, '-')}-${thread}.jsonl`), [
      { timestamp, type: 'turn_context', payload: { model: 'gpt-5.5' } },
      { timestamp, type: 'token_usage_record', payload: { session_id: thread, usage: {
        input_tokens: 1000, cached_input_tokens: 400, cache_write_input_tokens: 100, output_tokens: 50, reasoning_output_tokens: 20 } } },
    ].map(record => `${JSON.stringify(record)}\n`).join(''));
    if (scenario.fail) {
      emit({ type: 'turn.failed', error: { message: scenario.fail } });
      process.exit(1);
    }
    emit({ type: 'item.completed', item: { id: 'item_0', type: 'agent_message', text: 'Working on it.' } });
    emit({ type: 'item.completed', item: { id: 'item_1', type: 'agent_message', text: answer } });
    emit({ type: 'turn.completed', usage: { input_tokens: 1, cached_input_tokens: 0, output_tokens: 1 } });
  } else {
    const usage = { input_tokens: 10, cache_read_input_tokens: 300, cache_creation_input_tokens: 50, output_tokens: 40,
      output_tokens_details: { thinking_tokens: 5 } };
    const result = { type: 'result', subtype: 'success', session_id: thread, usage, total_cost_usd: 0.0123 };
    emit(scenario.fail ? { ...result, is_error: true, result: scenario.fail }
      : { ...result, is_error: false, result: answer, ...typeof output === 'object' ? { structured_output: output } : {} });
    process.exit(scenario.fail ? 1 : 0);
  }
}
