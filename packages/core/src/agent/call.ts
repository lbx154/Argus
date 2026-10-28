// One backend turn: run the CLI (under bubblewrap where it starts) with the prompt on stdin and
// its final answer held to the role's form, read its JSON events, judge the form it ended with,
// record its raw output and usage.

import { randomBytes } from 'node:crypto';
import { appendFileSync, createWriteStream, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { isJsonObject, type PayloadSchema } from '@argus/contracts';
import { executeProcess, type ProcessExit } from '@argus/runtime';
import type { AgentRequest, AgentResult, Backend, Usage } from '../types.js';
import { claude } from './claude.js';
import { codex } from './codex.js';
import { copilot } from './copilot.js';
import { formSchema, judge } from './handoff.js';
import { pi } from './pi.js';
import { agentEnv, confine, launcher, sandboxed } from './sandbox.js';

export interface BackendRun {
  /** The CLI's arguments; the prompt goes on stdin. */
  args: string[];
  /** Settings the CLI takes only from its environment. */
  env?: Record<string, string>;
  consume(event: Record<string, unknown>): void;
  /** What the turn came to, once the CLI has exited; `output` is its final answer, the form as filled in. */
  finish(): Pick<AgentResult, 'threadId' | 'completed' | 'error' | 'model' | 'usage'> & { output: unknown };
}

export type StartBackend = (
  request: AgentRequest,
  call: { confined: boolean; started: number; schema: PayloadSchema; /** calls/<call>, the stem of any file the call needs. */ stem: string },
) => BackendRun;

const START: Record<Backend, StartBackend> = { codex, claude, copilot, pi };

export async function callAgent(request: AgentRequest): Promise<AgentResult> {
  const started = Date.now();
  const callId = `${new Date(started).toISOString().replace(/\D/g, '').slice(0, 14)}-${randomBytes(3).toString('hex')}`;
  mkdirSync(request.callsDir, { recursive: true });
  const stem = join(request.callsDir, callId);
  const raw = createWriteStream(`${stem}.jsonl`);
  try {
    const confined = sandboxed();
    const run = START[request.backend](request, { confined, started, schema: formSchema(request.form), stem });
    const argv = confined ? confine([request.backend, ...run.args], request) : [...launcher(request.backend), ...run.args];
    const stderr: string[] = [];
    let exit: ProcessExit | undefined;
    for await (const item of executeProcess({
      executable: argv[0]!, args: argv.slice(1), cwd: request.cwd, input: request.prompt,
      env: { ...agentEnv(request.backend), ...run.env }, ...request.signal ? { signal: request.signal } : {},
    })) {
      if (item.type === 'exit') { exit = item; continue; }
      raw.write(`${item.stream === 'stdout' ? item.line : JSON.stringify({ stderr: item.line })}\n`);
      if (item.stream === 'stderr') { if (stderr.push(item.line) > 20) stderr.shift(); continue; }
      let event: unknown;
      try { event = JSON.parse(item.line); } catch { continue; }
      if (isJsonObject(event)) run.consume(event);
    }
    const { output, ...turn } = run.finish();
    const verdict = turn.completed ? judge(request.form, output, request.check) : null;
    const result: AgentResult = {
      callId, ...turn, handoff: typeof verdict === 'string' ? null : verdict, ms: Date.now() - started,
      // A refused form says why; a failed turn its cause, in order: a stop (cancelled), the
      // backend's own words, its stderr, its exit.
      error: typeof verdict === 'string' ? verdict : turn.completed ? null : (exit?.stopKind ? exit.error : null) ?? turn.error
        ?? (stderr.join('\n').trim() || `${request.backend} exited with ${exit?.code ?? exit?.signal ?? 'no status'}`),
    };
    mkdirSync(dirname(request.usageFile), { recursive: true });
    appendFileSync(request.usageFile, `${JSON.stringify({
      ts: Date.now() / 1000, callId, role: request.role, backend: request.backend, model: result.model,
      tokens: result.usage && tokens(result.usage), cost_usd: result.usage?.cost_usd ?? null,
    })}\n`);
    return result;
  } finally {
    await new Promise(resolve => raw.end(resolve));
  }
}

const tokens = ({ cost_usd: _, ...counts }: Usage): Omit<Usage, 'cost_usd'> => counts;
