#!/usr/bin/env node
// Stands in for `codex app-server`, `claude -p --input-format stream-json`, `copilot
// --output-format json` and `pi --mode json`. Each turn's prompt is a scenario, and the turn ends
// with the scenario's output: as its final answer, as codex and claude do under an output schema,
// or, for copilot and pi, as the arguments of a call to the hand-off tool the call's extension
// registers, loaded as that CLI loads it. Codex and claude take turn after turn until their input
// ends; copilot and pi take one. Beside the workdir it notes the schema it was held to, the
// credentials it could see, whether the tool's call ends the turn, the instructions it was given
// (in its own place for them, or above the scenario), and which run of the CLI it was.
import { spawn } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { register } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';

const args = process.argv.slice(2);
const cli = args[0] === 'app-server' ? 'codex' : args.includes('--plugin-dir') ? 'copilot' : args.includes('--extension') ? 'pi' : 'claude';
const emit = event => process.stdout.write(`${JSON.stringify(event)}\n`);
const option = name => args[args.indexOf(name) + 1];
const run = randomUUID();
const note = seen => writeFileSync(join(process.cwd(), `seen-${cli}.json`), JSON.stringify({
  ...seen, run, env: Object.keys(process.env).filter(name => /^(GH_TOKEN|MY_SECRET|ANTHROPIC_API_KEY|COPILOT_GITHUB_TOKEN)$/.test(name)),
}));

/** The hand-off tool the call's extension registers, or null where copilot loads no extension. */
async function handOffTool() {
  if (cli === 'pi') {
    let tool;
    (await import(pathToFileURL(option('--extension')).href)).default({ registerTool: registered => { tool = registered; } });
    return { ...tool, ends: (await tool.execute()).terminate === true };
  }
  if (!/\bEXTENSIONS\b/.test(process.env.COPILOT_CLI_ENABLED_FEATURE_FLAGS ?? '')) return null;
  const sdk = `data:text/javascript,${encodeURIComponent('export const joinSession = async config => { globalThis.joined = config; };')}`;
  register(`data:text/javascript,${encodeURIComponent(`export const resolve = (specifier, context, next) =>
    specifier === '@github/copilot-sdk/extension' ? { url: ${JSON.stringify(sdk)}, shortCircuit: true } : next(specifier, context);`)}`);
  await import(pathToFileURL(join(option('--plugin-dir'), 'extensions', 'argus', 'extension.mjs')).href);
  const [tool] = globalThis.joined.tools;
  return { ...tool, ends: tool.isTerminal === true };
}

const uuid7 = () => {
  const hex = Date.now().toString(16).padStart(12, '0') + randomBytes(10).toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-7${hex.slice(13, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
};

/** Starts what a scenario leaves running: jobs, as `nohup … &` and `setsid` start them, each noting
 * in job-<how>.txt when it starts and, `ms` later, when it ends, and holding a connection to `port`
 * if there is one; or for `hang`, one that runs as long as its connection to that port is open. */
function leave(scenario) {
  for (const how of scenario.jobs ? ['nohup', 'setsid'] : []) {
    const { ms, port } = scenario.jobs;
    const file = JSON.stringify(join(process.cwd(), `job-${how}.txt`));
    spawn(process.execPath, ['-e', `const { appendFileSync } = require('node:fs'); appendFileSync(${file}, 'started\\n');
      ${port ? `require('node:net').connect(${port}, '127.0.0.1');` : ''}
      setTimeout(() => { appendFileSync(${file}, 'ended\\n'); process.exit(); }, ${ms});`], { stdio: 'ignore', detached: how === 'setsid' }).unref();
  }
  if (scenario.hang) spawn(process.execPath, ['-e', `require('node:net').connect(${scenario.hang}, '127.0.0.1')`], { stdio: 'ignore' });
}

const scenarioOf = text => JSON.parse(text.trimEnd().split('\n').at(-1));
const answerOf = output => typeof output === 'string' ? output : JSON.stringify(output);

if (cli === 'codex') {
  // JSON-RPC over stdin and stdout, as the app-server speaks it.
  let thread = null;
  let instructions;
  /** For a turn that waits for a letter: how it goes on once one comes. */
  let steerable = null;
  const reply = (id, result) => emit({ id, result });
  const notify = (method, params) => emit({ method, params: { threadId: thread, ...params } });
  createInterface({ input: process.stdin }).on('line', line => {
    const { id, method, params } = JSON.parse(line);
    if (method === 'initialize') return reply(id, { userAgent: 'fake' });
    if (method === 'thread/start' || method === 'thread/resume') {
      thread = method === 'thread/resume' ? params.threadId : uuid7();
      instructions = params.developerInstructions ?? undefined;
      return reply(id, { thread: { id: thread }, model: 'gpt-5.5' });
    }
    if (method === 'turn/steer') {
      if (!steerable || params.expectedTurnId !== 'turn-1') return emit({ id, error: { code: -32600, message: 'no active turn' } });
      reply(id, { turnId: 'turn-1' });
      return steerable(params.input[0].text);
    }
    if (method !== 'turn/start') return;
    reply(id, { turn: { id: 'turn-1', status: 'inProgress' } });
    const scenario = scenarioOf(params.input[0].text);
    const seen = { schema: params.outputSchema, instructions, model: params.model ?? undefined };
    note(seen);
    leave(scenario);
    if (scenario.hang) return;
    if (scenario.steer) {
      steerable = steered => { steerable = null; note({ ...seen, steered }); end(scenario); };
      return;
    }
    end(scenario);
  });
  const end = scenario => {
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
    if (scenario.fail) return notify('turn/completed', { turn: { id: 'turn-1', status: 'failed', error: { message: scenario.fail } } });
    notify('item/completed', { item: { id: 'item_0', type: 'agentMessage', text: 'Working on it.' } });
    notify('item/started', { item: { id: 'item_1', type: 'commandExecution', command: 'npm test', status: 'inProgress' } });
    notify('item/completed', { item: { id: 'item_1', type: 'commandExecution', command: 'npm test', exitCode: 0, status: 'completed' } });
    notify('item/completed', { item: { id: 'item_2', type: 'fileChange', changes: [{ path: 'parser.js', kind: 'update' }], status: 'completed' } });
    notify('item/completed', { item: { id: 'item_3', type: 'agentMessage', text: answerOf(scenario.output) } });
    notify('turn/completed', { turn: { id: 'turn-1', status: 'completed', error: null } });
  };
} else if (cli === 'claude') {
  // A user message a line, each answered by a turn ending in a result, whose cost is the session's so far.
  const session = (args.includes('--resume') && option('--resume')) || randomUUID();
  let spent = 0;
  createInterface({ input: process.stdin }).on('line', line => {
    const { message, uuid } = JSON.parse(line);
    // A turn of its own first, as claude takes one when a background job ends, and then the message played back.
    if (args.includes('--replay-user-messages')) {
      emit({ type: 'result', subtype: 'success', session_id: session, is_error: false, result: 'The job ended.', structured_output: { to: 'operator', letter: 'stray', open: [] } });
      emit({ type: 'user', uuid, isReplay: true, session_id: session, message });
    }
    const scenario = scenarioOf(message.content);
    note({ schema: JSON.parse(option('--json-schema')), instructions: args.includes('--append-system-prompt') ? option('--append-system-prompt') : undefined,
      model: args.includes('--model') ? option('--model') : undefined });
    leave(scenario);
    emit({ type: 'system', subtype: 'init', session_id: session, model: 'claude-test' });
    if (scenario.hang) return;
    const usage = { input_tokens: 10, cache_read_input_tokens: 300, cache_creation_input_tokens: 50, output_tokens: 40,
      output_tokens_details: { thinking_tokens: 5 } };
    const result = { type: 'result', subtype: 'success', session_id: session, usage, total_cost_usd: spent = Math.round((spent + 0.0123) * 1e4) / 1e4 };
    const said = (content, more = {}) => emit({ type: 'assistant', session_id: session, message: { role: 'assistant', content }, ...more });
    said([{ type: 'text', text: 'Working on it.' }, { type: 'tool_use', name: 'Bash', input: { command: 'npm test' } },
      { type: 'tool_use', name: 'Read', input: { file_path: 'README.md' } }, { type: 'tool_use', name: 'Edit', input: { file_path: 'parser.js' } }]);
    // A subagent's message is its own.
    said([{ type: 'text', text: 'Subagent at work.' }], { parent_tool_use_id: 'toolu_1' });
    const output = scenario.output;
    emit(scenario.fail ? { ...result, is_error: true, result: scenario.fail }
      : { ...result, is_error: false, result: answerOf(output), ...typeof output === 'object' ? { structured_output: output } : {} });
  });
} else {
  const input = readFileSync(0, 'utf8');
  const scenario = scenarioOf(input);
  const tool = await handOffTool();
  note({ schema: tool?.parameters, ends: tool?.ends, instructions: input.trimEnd().split('\n').slice(0, -1).join('\n').trim() || undefined });
  const resumed = cli === 'copilot' ? args.find(arg => arg.startsWith('--resume='))?.slice(9) : args.includes('--session') && option('--session');
  const thread = resumed || randomUUID();
  if (cli === 'pi') emit({ type: 'session', version: 3, id: thread, cwd: process.cwd() });
  leave(scenario);
  if (scenario.hang) setInterval(() => {}, 1000);
  else {
    const output = scenario.output;
    const answer = answerOf(output);
    // Copilot and pi call the tool when there is a form and a tool to carry it, and otherwise end in prose.
    const called = typeof output === 'object' && tool ? output : undefined;
    if (cli === 'copilot') {
      // The session's event log has a usage record per model request, a subagent's among them.
      const log = join(process.env.COPILOT_HOME || join(homedir(), '.copilot'), 'session-state', thread);
      mkdirSync(log, { recursive: true });
      const usage = { model: 'copilot-test', inputTokens: 1200, cacheReadTokens: 800, cacheWriteTokens: 100, outputTokens: 30,
        reasoningTokens: 10, copilotUsage: { totalNanoAiu: 2_500_000_000 } };
      appendFileSync(join(log, 'events.jsonl'), [{}, { agentId: 'sub-1' }].map(by => `${JSON.stringify({
        type: 'session.usage_record', ...by, timestamp: new Date().toISOString(), data: { usage } })}\n`).join(''));
      if (scenario.fail) {
        emit({ type: 'session.error', data: { errorType: 'rate_limit', message: scenario.fail } });
        emit({ type: 'result', sessionId: thread, exitCode: 1 });
        process.exit(1);
      }
      const message = (content, toolRequests = []) => ({ type: 'assistant.message', data: { model: 'copilot-test', content, toolRequests } });
      emit(message('Working on it.', [{ name: 'bash', arguments: { command: 'npm test' } }, { name: 'view', arguments: { path: 'README.md' } },
        { name: 'edit', arguments: { path: 'parser.js' } }]));
      emit(called ? message('', [{ toolCallId: 'call_1', name: 'hand_off', arguments: called, type: 'function' }]) : message(answer));
      // A background subagent reports after the turn's last message.
      emit({ ...message('Background check done.'), agentId: 'sub-1' });
      emit({ type: 'result', sessionId: thread, exitCode: 0 });
    } else {
      const usage = { input: 20, output: 40, cacheRead: 300, cacheWrite: 50, reasoning: 5, cost: { total: 0.0042 } };
      const message = (content, more = {}) => ({ type: 'message_end',
        message: { role: 'assistant', provider: 'anthropic', model: 'claude-test', stopReason: 'stop', content, usage, ...more } });
      emit(message([{ type: 'text', text: 'Working on it.' }, { type: 'toolCall', name: 'bash', arguments: { command: 'npm test' } },
        { type: 'toolCall', name: 'read', arguments: { path: 'README.md' } }, { type: 'toolCall', name: 'edit', arguments: { path: 'parser.js' } }]));
      // Pi settles and exits 0 after a failed request too.
      emit(scenario.fail ? message([], { stopReason: 'error', errorMessage: scenario.fail })
        : message(called ? [{ type: 'toolCall', id: 'call_1', name: 'hand_off', arguments: called }] : [{ type: 'text', text: answer }],
          called ? { stopReason: 'toolUse' } : {}));
      emit({ type: 'agent_settled', aborted: false });
    }
  }
}
