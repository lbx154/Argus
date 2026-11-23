// `claude -p --input-format stream-json --output-format stream-json`: each turn a user message,
// its final answer held to the form's schema, followed to its result event, which carries the
// turn's usage and the session's cost so far. What a turn leaves running in the background runs
// on while the CLI does.

import { randomUUID } from 'node:crypto';
import { isJsonObject } from '@argus/contracts';
import { count, type Usage } from '../types.js';
import type { StartBackend } from './call.js';
import { said, thought, used } from './steps.js';

export const claude: StartBackend = (request, { confined, schema }) => {
  const args = [
    '-p', '--verbose', '--input-format', 'stream-json', '--output-format', 'stream-json', '--replay-user-messages',
    ...request.model ? ['--model', request.model] : [],
    ...request.effort ? ['--effort', request.effort] : [],
    ...request.instructions ? ['--append-system-prompt', request.instructions] : [],
    // Inside bubblewrap the sandbox is bubblewrap's, and a reader's workdir is mounted read-only,
    // so it may run commands (a check) too; outside it, a reader only reads and a writer only edits.
    // A writer gets the tools the work uses and no more: every tool's definition, and every skill a Skill tool
    // lists, is read again at every step (a role's 24 tools and 18 skills were 9K tokens a request, used never).
    ...request.access === 'read' && !confined ? ['--tools', 'Read,Glob,Grep']
      : ['--tools', request.access === 'read' ? 'Read,Glob,Grep,Bash' : 'Bash,Read,Edit,Write,Glob,Grep,TaskStop', '--permission-mode', confined ? 'bypassPermissions' : 'acceptEdits'],
    ...[...request.readDirs ?? [], ...request.writeDirs ?? []].flatMap(dir => ['--add-dir', dir]),
    '--json-schema', JSON.stringify(schema),
    ...request.resume ? ['--resume', request.resume] : [],
  ];
  // The session is the one Claude reports: one it cannot resume is none, and the role starts afresh.
  let threadId: string | null = null;
  let model: string | null = null;
  /** What the CLI has cost so far, which each result reports. */
  let spent = 0;
  /** The turn under way: queues letters till our prompt is replayed, then resolves each as Claude replays it. */
  let active: {
    ready: boolean;
    queued: Array<[string, string]>;
    steers: Map<string, (ok: boolean) => void>;
    send: (id: string, text: string) => void;
  } | null = null;
  return {
    args, instructed: true,
    async turn(cli, prompt, { onStep, request }) {
      let result: Record<string, unknown> | null = null;
      // Claude takes a turn of its own when a background job it started ends; this turn's events
      // begin where its message is played back.
      const uuid = randomUUID();
      let mine = false;
      // The context each step reads, and whether this turn was told it passed the project's limit.
      let context: number | undefined;
      let told = false;
      const steps = new Set<string>();
      const state: NonNullable<typeof active> = {
        ready: false,
        queued: [],
        steers: new Map(),
        send: (id, text) => cli.send({ type: 'user', uuid: id, message: { role: 'user', content: text } }),
      };
      const finishSteers = (): void => {
        if (active === state) active = null;
        state.queued.length = 0;
        for (const resolve of state.steers.values()) resolve(false);
        state.steers.clear();
      };
      active = state;
      try {
        const ended = cli.until(event => {
          if (event.type === 'system' && event.subtype === 'init') {
            if (typeof event.session_id === 'string' && event.session_id) threadId = event.session_id;
            if (typeof event.model === 'string') model = event.model;
          }
          if (!mine) {
            mine = event.type === 'user' && event.uuid === uuid;
            if (mine) {
              if (typeof event.session_id === 'string' && event.session_id) threadId = event.session_id;
              state.ready = true;
              for (const [id, text] of state.queued.splice(0)) state.send(id, text);
            }
            return false;
          }
          if (typeof event.session_id === 'string' && event.session_id) threadId = event.session_id;
          if (event.type === 'user' && typeof event.uuid === 'string' && state.steers.has(event.uuid)) {
            state.steers.get(event.uuid)!(true);
            state.steers.delete(event.uuid);
          }
          // What a subagent runs and edits are the role's steps; its words and thoughts are its own.
          // A thought is Claude's summary of its thinking, where it gives one.
          const content = event.type === 'assistant' && isJsonObject(event.message) && Array.isArray(event.message.content)
            ? event.message.content.filter(isJsonObject) : [];
          if (content.some(part => part.type === 'tool_use' && part.name === 'StructuredOutput')) finishSteers();
          if (event.type === 'assistant' && !event.parent_tool_use_id && isJsonObject(event.message) && typeof event.message.id === 'string'
            && !steps.has(event.message.id) && isJsonObject(event.message.usage)) {
            steps.add(event.message.id);
            const read = event.message.usage;
            context = count(read.input_tokens) + count(read.cache_read_input_tokens) + count(read.cache_creation_input_tokens);
            // Claude reads a user message sent during a turn at its next step, in the same turn.
            if (!told && request.freshAt && request.overflow && context > request.freshAt) {
              told = true;
              cli.send({ type: 'user', message: { role: 'user', content: request.overflow.replace('{context}', String(context)) } });
            }
          }
          for (const part of content) {
            onStep(part.type === 'text' && !event.parent_tool_use_id ? said(part.text) : part.type === 'tool_use' ? used(part.name, part.input)
              : part.type === 'thinking' && !event.parent_tool_use_id ? thought([part.thinking]) : null);
          }
          if (event.type !== 'result') return false;
          finishSteers();
          result = event;
          return true;
        });
        cli.send({ type: 'user', uuid, message: { role: 'user', content: prompt } });
        const finished = await ended;
        const ending: Record<string, unknown> = result ?? {};
        const completed = finished && ending.subtype === 'success' && !ending.is_error;
        const errors = Array.isArray(ending.errors) ? ending.errors.filter((line): line is string => typeof line === 'string').join('; ').trim() : '';
        const words = (typeof ending.result === 'string' ? ending.result.trim() : '') || errors;
        const reported = isJsonObject(ending.usage) ? ending.usage : null;
        const total = typeof ending.total_cost_usd === 'number' ? ending.total_cost_usd : null;
        // Anthropic counts input apart from cache reads and writes; Argus's input includes both.
        const usage: Usage | null = reported && {
          input_tokens: count(reported.input_tokens) + count(reported.cache_read_input_tokens) + count(reported.cache_creation_input_tokens),
          cached_input_tokens: count(reported.cache_read_input_tokens),
          output_tokens: count(reported.output_tokens),
          reasoning_output_tokens: count(isJsonObject(reported.output_tokens_details) ? reported.output_tokens_details.thinking_tokens : 0),
          cost_usd: total === null ? null : Math.max(0, total - spent),
        };
        if (total !== null && total > spent) spent = total;
        return {
          threadId, completed, model: model ?? request.model ?? null, usage, ...context !== undefined ? { context } : {}, output: ending.structured_output ?? undefined,
          error: completed ? null : !finished ? cli.why() : words || `Claude reported ${String(ending.subtype ?? 'an error')}.`,
        };
      } finally {
        finishSteers();
      }
    },
    async steer(_cli, text) {
      if (!active) return false;
      const current = active;
      const id = randomUUID();
      return new Promise<boolean>(resolve => {
        current.steers.set(id, resolve);
        if (current.ready) current.send(id, text);
        else current.queued.push([id, text]);
      });
    },
  };
};
