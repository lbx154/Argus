// `codex app-server`: the session a thread on it, each turn a `turn/start` whose final answer is
// held to the form's schema, followed by the thread's notifications to `turn/completed`; usage
// from the session's rollout files. What a turn leaves running in a background terminal runs on
// while the app-server does.

import { readdirSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { isJsonObject } from '@argus/contracts';
import { quoteTokenUsage } from '@argus/runtime';
import { count, type Usage } from '../types.js';
import type { StartBackend } from './call.js';
import { privatePath } from './sandbox.js';
import { said, used } from './steps.js';

export const codex: StartBackend = (request, { confined, schema }) => {
  const args = [
    'app-server',
    // An inherited desktop notify hook would hold every turn; a background terminal lives as long as the session.
    '-c', 'notify=[]', '-c', 'background_terminal_max_timeout=9223372036854775807', '-c', 'approval_policy="never"',
    // Inside bubblewrap the sandbox is bubblewrap's, which mounts a reader's workdir read-only and
    // leaves it /tmp to run a check in; outside it, codex's own. On Windows that is off, and every
    // command refused, until one is chosen: this one needs no administrator.
    '-c', 'windows.sandbox="unelevated"',
    ...confined ? ['-c', 'sandbox_mode="danger-full-access"']
      : request.access !== 'write' ? ['-c', 'sandbox_mode="read-only"']
      : ['-c', 'sandbox_mode="workspace-write"', '-c', 'sandbox_workspace_write.network_access=true',
        '-c', `sandbox_workspace_write.writable_roots=${JSON.stringify(request.writeDirs ?? [])}`],
  ];
  let thread = request.resume ?? null;
  let ready = false;
  let model: string | null = null;
  /** The turn under way, which a letter can still reach. */
  let running: unknown = null;
  return {
    args, instructed: true,
    async turn(cli, prompt, { request: now, onStep, started }) {
      if (!ready) {
        await cli.request('initialize', { clientInfo: { name: 'argus', title: 'Argus', version: '1' }, capabilities: null });
        cli.send({ method: 'initialized' });
        const settings = { cwd: request.cwd, developerInstructions: request.instructions ?? null };
        let opened;
        try {
          opened = thread ? await cli.request('thread/resume', { threadId: thread, ...settings, excludeTurns: true })
            : await cli.request('thread/start', settings);
        } catch (refused) {
          // A thread Codex cannot resume is none: the role starts afresh at its next turn.
          if (cli.gone) throw refused;
          return { threadId: null, completed: false, error: String(refused instanceof Error ? refused.message : refused), output: undefined, model: null, usage: null };
        }
        thread = isJsonObject(opened.thread) && typeof opened.thread.id === 'string' ? opened.thread.id : null;
        if (!thread) throw new Error('Codex opened no thread.');
        model = typeof opened.model === 'string' ? opened.model : null;
        ready = true;
      }
      let text = '';
      let error: string | null = null;
      let status = '';
      // This turn's events only: once it is named, none of another turn of the thread's. The CLI
      // runs this session alone, so another thread on it is a subagent the turn started: what it
      // runs and edits are the role's steps, though its words and its end are its own.
      let turnId: unknown = null;
      const ended = cli.until(({ method, params }) => {
        if (!isJsonObject(params)) return false;
        const own = params.threadId === thread;
        const of = isJsonObject(params.turn) ? params.turn.id : params.turnId;
        if (own && turnId !== null && of !== undefined && of !== turnId) return false;
        const item = isJsonObject(params.item) ? params.item : {};
        if (method === 'item/started' && item.type === 'commandExecution') onStep(used(item.type, item));
        else if (method === 'item/completed' && item.type === 'fileChange' && Array.isArray(item.changes)) {
          for (const { path } of item.changes.filter(isJsonObject)) onStep(typeof path === 'string' ? { kind: 'edit', text: path } : null);
        } else if (!own) return false;
        else if (method === 'item/completed' && item.type === 'agentMessage') onStep(said(text = String(item.text ?? '')));
        else if (method === 'error' && isJsonObject(params.error) && params.willRetry !== true) error = String(params.error.message ?? error);
        else if (method === 'turn/completed' && isJsonObject(params.turn)) {
          status = String(params.turn.status);
          if (isJsonObject(params.turn.error)) error = String(params.turn.error.message ?? error);
          return true;
        }
        return false;
      });
      let turn;
      try {
        ({ turn } = await cli.request('turn/start', {
          threadId: thread, input: [{ type: 'text', text: prompt, text_elements: [] }], outputSchema: schema,
          model: now.model ?? null, effort: now.effort ?? null,
        }));
      } catch (refused) {
        // A turn Codex refuses (settings it does not take, say) fails alone: the session goes on.
        if (cli.gone) throw refused;
        return { threadId: thread, completed: false, error: String(refused instanceof Error ? refused.message : refused), output: undefined, model, usage: null };
      }
      running = turnId = isJsonObject(turn) ? turn.id : null;
      const finished = await ended;
      running = null;
      const sessions = join(process.env.CODEX_HOME || join(homedir(), '.codex'), 'sessions');
      const counted = codexUsage(confined ? privatePath(sessions, request.cwd) : sessions, thread!, started);
      return {
        threadId: thread, completed: finished && status === 'completed', output: form(text), model: counted.model ?? model ?? request.model ?? null, usage: counted.usage,
        error: !finished ? cli.why() : status === 'completed' ? null : error ?? `Codex reported the turn ${status}.`,
      };
    },
    async steer(cli, text) {
      if (running === null) return false;
      try {
        await cli.request('turn/steer', { threadId: thread, expectedTurnId: running, input: [{ type: 'text', text, text_elements: [] }] });
        return true;
      } catch { return false; }
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
