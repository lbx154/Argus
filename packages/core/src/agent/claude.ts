// `claude -p --output-format stream-json`: the prompt on stdin, the final answer held to the
// form's schema, usage and cost from the result event.

import { isJsonObject } from '@argus/contracts';
import { count, type Usage } from '../types.js';
import type { StartBackend } from './call.js';

export const claude: StartBackend = (request, { confined, schema }) => {
  const args = [
    '-p', '--verbose', '--output-format', 'stream-json',
    ...request.model ? ['--model', request.model] : [],
    ...request.effort ? ['--effort', request.effort] : [],
    // Inside bubblewrap the sandbox is bubblewrap's, and a reader's workdir is mounted read-only,
    // so it may run commands (a check) too; outside it, a reader only reads and a writer only edits.
    ...request.access === 'read' && !confined ? ['--tools', 'Read,Glob,Grep']
      : [...request.access === 'read' ? ['--tools', 'Read,Glob,Grep,Bash'] : [], '--permission-mode', confined ? 'bypassPermissions' : 'acceptEdits'],
    ...[...request.readDirs ?? [], ...request.writeDirs ?? []].flatMap(dir => ['--add-dir', dir]),
    '--json-schema', JSON.stringify(schema),
    ...request.resume ? ['--resume', request.resume] : [],
  ];
  let threadId = request.resume ?? null;
  let model: string | null = null;
  let result: Record<string, unknown> | null = null;
  return {
    args,
    consume(event) {
      if (typeof event.session_id === 'string' && event.session_id) threadId = event.session_id;
      if (event.type === 'system' && event.subtype === 'init' && typeof event.model === 'string') model = event.model;
      if (event.type === 'result') result = event;
    },
    finish() {
      const completed = result?.subtype === 'success' && !result.is_error;
      const said = typeof result?.result === 'string' ? result.result.trim() : '';
      const reported = isJsonObject(result?.usage) ? result.usage : null;
      // Anthropic counts input apart from cache reads and writes; Argus's input includes both.
      const usage: Usage | null = reported && {
        input_tokens: count(reported.input_tokens) + count(reported.cache_read_input_tokens) + count(reported.cache_creation_input_tokens),
        cached_input_tokens: count(reported.cache_read_input_tokens),
        output_tokens: count(reported.output_tokens),
        reasoning_output_tokens: count(isJsonObject(reported.output_tokens_details) ? reported.output_tokens_details.thinking_tokens : 0),
        cost_usd: typeof result?.total_cost_usd === 'number' ? result.total_cost_usd : null,
      };
      return {
        threadId, completed, model: model ?? request.model ?? null, usage,
        error: completed ? null : result ? said || `Claude reported ${String(result.subtype ?? 'an error')}.` : null,
        output: result?.structured_output ?? undefined,
      };
    },
  };
};
