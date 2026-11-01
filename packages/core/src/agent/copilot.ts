// `copilot --output-format json`: one turn per CLI, the prompt on stdin, the form a tool whose call
// ends the turn (an extension tool with isTerminal, loaded from a plugin written for the call;
// copilot does not check its arguments against the schema), usage from the session's event log.
// Its ACP mode, which keeps a CLI between turns, offered the model neither an extension's tool
// nor an MCP server's when tried (October 2026), so the hand-off has no way in there.

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { isJsonObject } from '@argus/contracts';
import { count, type Step, type Usage } from '../types.js';
import type { StartBackend } from './call.js';
import { handOffTool } from './handoff.js';
import { privatePath } from './sandbox.js';
import { said, thought, used } from './steps.js';
import { MessageStream } from './streaming.js';

export const copilot: StartBackend = (request, { confined, schema, stem }) => {
  const tool = handOffTool(schema);
  const plugin = `${stem}.copilot`;
  mkdirSync(join(plugin, 'extensions', 'argus'), { recursive: true });
  writeFileSync(join(plugin, 'plugin.json'), JSON.stringify({ name: 'argus' }));
  writeFileSync(join(plugin, 'extensions', 'argus', 'extension.mjs'), [
    `import { joinSession } from '@github/copilot-sdk/extension';`,
    `const tool = ${JSON.stringify({ ...tool, isTerminal: true, defer: 'never' })};`,
    `await joinSession({ tools: [{ ...tool, handler: () => 'Handed off.' }] });`, '',
  ].join('\n'));
  const args = [
    '--output-format', 'json', '--stream', 'on', '--no-ask-user',
    ...request.model ? ['--model', request.model] : [],
    ...request.effort ? ['--reasoning-effort', request.effort] : [],
    // Inside bubblewrap the sandbox is bubblewrap's; outside it, copilot's own path checks. A
    // turn that may not write sees only the tools that read.
    request.access === 'write' && confined ? '--yolo' : '--allow-all-tools',
    ...request.access === 'read' ? [`--available-tools=view,rg,glob,${tool.name}`] : [],
    ...[...request.readDirs ?? [], ...request.writeDirs ?? []].flatMap(dir => ['--add-dir', dir]),
    '--plugin-dir', plugin,
    ...request.resume ? [`--resume=${request.resume}`] : [],
  ];
  let threadId = request.resume ?? null;
  let completed = false;
  let error: string | null = null;
  let last: Record<string, unknown> | null = null;
  const consume = (event: Record<string, unknown>): (Step | null)[] => {
    if (event.agentId !== undefined) return []; // a subagent's
    const data = isJsonObject(event.data) ? event.data : {};
    if (event.type === 'assistant.message') {
      last = data;
      const calls = Array.isArray(data.toolRequests) ? data.toolRequests.filter(isJsonObject) : [];
      // What it thought first, as copilot sums it up, then what it says and runs.
      return [thought([data.reasoningText]), said(data.content), ...calls.map(call => used(call.name, call.arguments))];
    } else if (event.type === 'session.error' && typeof data.message === 'string') error = data.message;
    else if (event.type === 'result') {
      if (typeof event.sessionId === 'string') threadId = event.sessionId;
      completed = event.exitCode === 0;
    }
    return [];
  };
  return {
    args,
    env: {
      // Extensions are an experimental feature, enabled for this run only.
      COPILOT_CLI_ENABLED_FEATURE_FLAGS: [process.env.COPILOT_CLI_ENABLED_FEATURE_FLAGS, 'EXTENSIONS'].filter(Boolean).join(','),
      // A background task waits as long as its turn.
      COPILOT_TASK_WAIT_TIMEOUT_SECONDS: '2147483.647',
    },
    async turn(cli, prompt, { onStep, started, request: now }) {
      const stream = new MessageStream(now.onWords);
      await cli.once(prompt, event => {
        if (event.agentId === undefined && isJsonObject(event.data)) {
          if (event.type === 'assistant.message_delta' && typeof event.data.deltaContent === 'string') stream.text(0, event.data.deltaContent);
          if (event.type === 'assistant.message') stream.clear();
        }
        for (const step of consume(event)) onStep(step);
        return event.type === 'result';
      });
      const requests = Array.isArray(last?.toolRequests) ? last.toolRequests.filter(isJsonObject) : [];
      const states = join(process.env.COPILOT_HOME || join(homedir(), '.copilot'), 'session-state');
      return {
        threadId, completed, error,
        output: requests.find(call => call.name === tool.name)?.arguments,
        model: typeof last?.model === 'string' ? last.model : request.model ?? null,
        usage: threadId ? copilotUsage(join(confined ? privatePath(states, request.cwd) : states, threadId, 'events.jsonl'), started) : null,
      };
    },
  };
};

/** Every model request the session recorded from `since` (epoch ms) on, its subagents' included,
 * an AI credit (1e9 nano) priced at $0.01. */
function copilotUsage(log: string, since: number): Usage | null {
  const usage: Usage = { input_tokens: 0, cached_input_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0, cost_usd: 0 };
  let requests = 0;
  let text = '';
  try { text = readFileSync(log, 'utf8'); } catch { return null; }
  for (const line of text.split('\n')) {
    if (!line.includes('"session.usage_record"')) continue;
    let record: unknown;
    try { record = JSON.parse(line); } catch { continue; } // a torn line is no request
    if (!isJsonObject(record) || typeof record.timestamp !== 'string' || Date.parse(record.timestamp) < since) continue;
    const row = isJsonObject(record.data) && isJsonObject(record.data.usage) ? record.data.usage : null;
    if (!row) continue;
    requests += 1;
    usage.input_tokens += count(row.inputTokens); // cache reads and writes included
    usage.cached_input_tokens += count(row.cacheReadTokens);
    usage.output_tokens += count(row.outputTokens);
    usage.reasoning_output_tokens += count(row.reasoningTokens);
    const nano = isJsonObject(row.copilotUsage) ? row.copilotUsage.totalNanoAiu : undefined;
    usage.cost_usd = typeof nano !== 'number' || usage.cost_usd === null ? null : usage.cost_usd + nano / 1e11;
  }
  return requests ? usage : null;
}
