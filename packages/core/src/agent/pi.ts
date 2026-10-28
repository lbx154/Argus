// `pi --mode json`: the prompt on stdin, the form a tool whose call ends the turn (an extension
// tool returning terminate; pi holds its arguments to the schema, so a malformed form is refused
// inside the turn), usage and cost from each assistant message.

import { writeFileSync } from 'node:fs';
import { isJsonObject } from '@argus/contracts';
import { count, type Usage } from '../types.js';
import type { StartBackend } from './call.js';
import { handOffTool } from './handoff.js';

export const pi: StartBackend = (request, { schema, stem }) => {
  const tool = handOffTool(schema);
  const extension = `${stem}.pi.mjs`;
  writeFileSync(extension, [
    `const tool = ${JSON.stringify(tool)};`,
    `export default pi => pi.registerTool({ ...tool, execute: async () => ({ content: [{ type: 'text', text: 'Handed off.' }], details: undefined, terminate: true }) });`, '',
  ].join('\n'));
  const args = [
    '--mode', 'json', '--extension', extension,
    ...request.model ? ['--model', request.model] : [],
    ...request.effort ? ['--thinking', request.effort] : [],
    // A turn that may not write sees only the tools that read.
    ...request.access === 'none' ? ['--tools', tool.name] : request.access === 'read' ? ['--tools', `read,grep,find,ls,${tool.name}`] : [],
    ...request.resume ? ['--session', request.resume] : [],
  ];
  let threadId = request.resume ?? null;
  let settled = false;
  let last: Record<string, unknown> | null = null;
  let model: string | null = null;
  let usage: Usage | null = null;
  return {
    args,
    consume(event) {
      if (event.type === 'session' && typeof event.id === 'string') threadId = event.id;
      else if (event.type === 'agent_settled') settled = event.aborted !== true;
      else if (event.type === 'message_end' && isJsonObject(event.message) && event.message.role === 'assistant') {
        const message = last = event.message;
        if (typeof message.model === 'string') model = typeof message.provider === 'string' ? `${message.provider}/${message.model}` : message.model;
        // Pi counts input apart from cache reads and writes; Argus's input includes both.
        const row: Record<string, unknown> = isJsonObject(message.usage) ? message.usage : {};
        const cost = isJsonObject(row.cost) ? row.cost.total : undefined;
        usage ??= { input_tokens: 0, cached_input_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0, cost_usd: 0 };
        usage.input_tokens += count(row.input) + count(row.cacheRead) + count(row.cacheWrite);
        usage.cached_input_tokens += count(row.cacheRead);
        usage.output_tokens += count(row.output);
        usage.reasoning_output_tokens += count(row.reasoning);
        usage.cost_usd = typeof cost !== 'number' || usage.cost_usd === null ? null : usage.cost_usd + cost;
      }
    },
    finish() {
      // Pi settles after a failed request too, and exits 0: the last answer says how it ended.
      const failed = last?.stopReason === 'error' || last?.stopReason === 'aborted';
      const content = Array.isArray(last?.content) ? last.content.filter(isJsonObject) : [];
      return {
        threadId, completed: settled && !failed, model: model ?? request.model ?? null, usage,
        error: failed ? String(last?.errorMessage || `Pi reported ${String(last?.stopReason)}.`) : null,
        output: content.find(part => part.type === 'toolCall' && part.name === tool.name)?.arguments,
      };
    },
  };
};
