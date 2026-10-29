// `claude -p --input-format stream-json --output-format stream-json`: each turn a user message,
// its final answer held to the form's schema, followed to its result event, which carries the
// turn's usage and the session's cost so far. What a turn leaves running in the background runs
// on while the CLI does.

import { isJsonObject } from '@argus/contracts';
import { count, type Usage } from '../types.js';
import type { StartBackend } from './call.js';
import { said, used } from './steps.js';

export const claude: StartBackend = (request, { confined, schema }) => {
  const args = [
    '-p', '--verbose', '--input-format', 'stream-json', '--output-format', 'stream-json',
    ...request.model ? ['--model', request.model] : [],
    ...request.effort ? ['--effort', request.effort] : [],
    ...request.instructions ? ['--append-system-prompt', request.instructions] : [],
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
  /** What the CLI has cost so far, which each result reports. */
  let spent = 0;
  return {
    args, instructed: true,
    async turn(cli, prompt, { onStep }) {
      let result: Record<string, unknown> | null = null;
      const ended = cli.until(event => {
        if (typeof event.session_id === 'string' && event.session_id) threadId = event.session_id;
        if (event.type === 'system' && event.subtype === 'init' && typeof event.model === 'string') model = event.model;
        // A subagent's messages are its own; the role's steps are in its own.
        const content = event.type === 'assistant' && !event.parent_tool_use_id && isJsonObject(event.message) && Array.isArray(event.message.content)
          ? event.message.content.filter(isJsonObject) : [];
        for (const part of content) onStep(part.type === 'text' ? said(part.text) : part.type === 'tool_use' ? used(part.name, part.input) : null);
        if (event.type !== 'result') return false;
        result = event;
        return true;
      });
      cli.send({ type: 'user', message: { role: 'user', content: prompt } });
      const finished = await ended;
      const ending: Record<string, unknown> = result ?? {};
      const completed = finished && ending.subtype === 'success' && !ending.is_error;
      const words = typeof ending.result === 'string' ? ending.result.trim() : '';
      const reported = isJsonObject(ending.usage) ? ending.usage : null;
      const total = typeof ending.total_cost_usd === 'number' ? ending.total_cost_usd : null;
      // Anthropic counts input apart from cache reads and writes; Argus's input includes both.
      const usage: Usage | null = reported && {
        input_tokens: count(reported.input_tokens) + count(reported.cache_read_input_tokens) + count(reported.cache_creation_input_tokens),
        cached_input_tokens: count(reported.cache_read_input_tokens),
        output_tokens: count(reported.output_tokens),
        reasoning_output_tokens: count(isJsonObject(reported.output_tokens_details) ? reported.output_tokens_details.thinking_tokens : 0),
        cost_usd: total === null ? null : total - spent,
      };
      if (total !== null) spent = total;
      return {
        threadId, completed, model: model ?? request.model ?? null, usage, output: ending.structured_output ?? undefined,
        error: completed ? null : !finished ? cli.why() : words || `Claude reported ${String(ending.subtype ?? 'an error')}.`,
      };
    },
  };
};
