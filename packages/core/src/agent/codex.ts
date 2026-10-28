// `codex exec --json`: the prompt on stdin, the final answer held to the form's schema, usage
// from the session's rollout files.

import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { isJsonObject } from '@argus/contracts';
import { quoteTokenUsage } from '@argus/runtime';
import type { Usage } from '../types.js';
import type { StartBackend } from './call.js';
import { privatePath } from './sandbox.js';

const count = (value: unknown): number => typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.trunc(value) : 0;

export const codex: StartBackend = (request, { confined, started, schema, stem }) => {
  const schemaFile = `${stem}.schema.json`;
  writeFileSync(schemaFile, JSON.stringify(schema));
  const args = [
    'exec', ...request.resume ? ['resume'] : [], '--json', '--skip-git-repo-check',
    // An inherited desktop notify hook would hold every turn; a background terminal waits as long as its turn.
    '-c', 'notify=[]', '-c', 'background_terminal_max_timeout=9223372036854775807',
    ...request.model ? ['-m', request.model] : [],
    ...request.effort ? ['-c', `model_reasoning_effort=${JSON.stringify(request.effort)}`] : [],
    // Inside bubblewrap the sandbox is bubblewrap's; outside it, codex's own. On Windows that is off,
    // and every command refused, until one is chosen: this one needs no administrator.
    '-c', 'windows.sandbox="unelevated"',
    ...request.access !== 'write' ? ['-c', 'sandbox_mode="read-only"']
      : confined ? ['--dangerously-bypass-approvals-and-sandbox']
      : ['-c', 'sandbox_mode="workspace-write"', '-c', 'sandbox_workspace_write.network_access=true',
        '-c', `sandbox_workspace_write.writable_roots=${JSON.stringify(request.writeDirs ?? [])}`],
    ...request.access === 'none' ? ['-c', 'features.shell_tool=false'] : [],
    '--output-schema', schemaFile,
    ...request.resume ? [request.resume] : [], '-',
  ];
  let threadId = request.resume ?? null;
  let completed = false;
  let failed = false;
  let error: string | null = null;
  let text = '';
  return {
    args,
    consume(event) {
      if (event.type === 'thread.started' && typeof event.thread_id === 'string') threadId = event.thread_id;
      else if (event.type === 'item.completed' && isJsonObject(event.item) && event.item.type === 'agent_message') text = String(event.item.text ?? '');
      else if (event.type === 'turn.completed') completed = true;
      else if (event.type === 'turn.failed') {
        failed = true;
        const message = isJsonObject(event.error) ? event.error.message : undefined;
        error = typeof message === 'string' && message.trim() ? message : error ?? 'Codex reported a failed turn.';
      } else if (event.type === 'error' && !failed && typeof event.message === 'string') error = event.message;
    },
    finish() {
      const sessions = join(process.env.CODEX_HOME || join(homedir(), '.codex'), 'sessions');
      const { usage, model } = threadId
        ? codexUsage(confined ? privatePath(sessions, request.cwd) : sessions, threadId, started)
        : { usage: null, model: null };
      return { threadId, completed: completed && !failed, error, output: form(text), model: model ?? request.model ?? null, usage };
    },
  };
};

/** The form in the turn's last message. */
function form(text: string): unknown {
  try { return JSON.parse(text); } catch { return undefined; }
}

/** Codex keeps a thread in YYYY/MM/DD/rollout-<time>-<id>.jsonl of the day it began, which its
 * UUIDv7 id carries; the day is local, or UTC's where codex cannot tell the local offset. */
function sessionFiles(sessions: string, thread: string): string[] {
  const began = new Date(parseInt(thread.replace(/-/g, '').slice(0, 12), 16));
  if (Number.isNaN(began.getTime())) return [];
  const pad = (value: number): string => String(value).padStart(2, '0');
  const days = new Set([
    `${began.getFullYear()}/${pad(began.getMonth() + 1)}/${pad(began.getDate())}`,
    `${began.getUTCFullYear()}/${pad(began.getUTCMonth() + 1)}/${pad(began.getUTCDate())}`,
  ]);
  return [...days].flatMap(day => {
    try {
      return readdirSync(join(sessions, day)).filter(name => name.startsWith('rollout-') && name.endsWith(`-${thread}.jsonl`))
        .map(name => join(sessions, day, name));
    } catch { return []; }
  });
}

/** Every model request codex recorded for `thread` from `since` (epoch ms) on, its subagents'
 * included: turn.completed counts the thread's whole life and leaves the subagents out. */
function codexUsage(sessions: string, thread: string, since: number): { usage: Usage | null; model: string | null } {
  const usage: Usage = { input_tokens: 0, cached_input_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0, cost_usd: 0 };
  let requests = 0;
  let model: string | null = null;
  const threads = [thread];
  for (const current of threads) { // grows by the subagents each file names
    for (const file of sessionFiles(sessions, current)) {
      let fileModel = '';
      for (const line of readFileSync(file, 'utf8').split('\n')) {
        if (!/"(turn_context|token_usage_record|agent_thread_id)"/.test(line)) continue;
        let record: unknown;
        try { record = JSON.parse(line); } catch { continue; } // a torn line is no request
        if (!isJsonObject(record) || !isJsonObject(record.payload)) continue;
        const payload = record.payload;
        if (record.type === 'turn_context' && typeof payload.model === 'string') {
          fileModel = payload.model;
          if (current === thread) model = fileModel;
        }
        if (typeof record.timestamp !== 'string' || Date.parse(record.timestamp) < since) continue;
        if (record.type === 'token_usage_record' && payload.session_id === thread && isJsonObject(payload.usage)) {
          const row = payload.usage;
          const counts = {
            input_tokens: count(row.input_tokens), cached_input_tokens: count(row.cached_input_tokens),
            cache_write_tokens: count(row.cache_write_input_tokens), output_tokens: count(row.output_tokens),
          };
          requests += 1;
          usage.input_tokens += counts.input_tokens;
          usage.cached_input_tokens += counts.cached_input_tokens;
          usage.output_tokens += counts.output_tokens;
          usage.reasoning_output_tokens += count(row.reasoning_output_tokens);
          // Each request is priced alone: the long-context tier is per request.
          const cost = quoteTokenUsage(fileModel, counts).cost_usd;
          usage.cost_usd = cost === null || usage.cost_usd === null ? null : usage.cost_usd + cost;
        }
        const agent = isJsonObject(payload.item) ? payload.item.agent_thread_id : undefined;
        if (typeof agent === 'string' && !threads.includes(agent)) threads.push(agent);
      }
    }
  }
  return { usage: requests ? usage : null, model };
}
