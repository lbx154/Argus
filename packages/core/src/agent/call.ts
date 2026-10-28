// One backend turn: serve the hand-off tool, run the CLI (under bubblewrap where it starts)
// with the prompt on stdin, read its JSON events, record its raw output and usage.

import { randomBytes } from 'node:crypto';
import { appendFileSync, createWriteStream, mkdirSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { isJsonObject } from '@argus/contracts';
import { executeProcess, type ProcessExit } from '@argus/runtime';
import type { AgentRequest, AgentResult, Backend, Usage } from '../types.js';
import { claude } from './claude.js';
import { codex } from './codex.js';
import { serveHandOff, type HandOffEndpoint } from './handoff.js';
import { agentEnv, confine, sandboxed } from './sandbox.js';

export interface BackendRun {
  /** The CLI's arguments; the prompt goes on stdin. */
  args: string[];
  env?: Record<string, string>;
  consume(event: Record<string, unknown>): void;
  /** What the turn came to, once the CLI has exited. */
  finish(): Pick<AgentResult, 'threadId' | 'completed' | 'error' | 'text' | 'model' | 'usage'>;
}

export type StartBackend = (
  request: AgentRequest, endpoint: HandOffEndpoint,
  call: { confined: boolean; started: number; /** A 0600 file the call may hold the endpoint's token in. */ secret: string },
) => BackendRun;

const BACKENDS: Record<Backend, StartBackend> = { codex, claude };

export async function callAgent(request: AgentRequest): Promise<AgentResult> {
  const started = Date.now();
  const callId = `${new Date(started).toISOString().replace(/\D/g, '').slice(0, 14)}-${randomBytes(3).toString('hex')}`;
  mkdirSync(request.callsDir, { recursive: true });
  const secret = join(request.callsDir, `${callId}.mcp.json`);
  const raw = createWriteStream(join(request.callsDir, `${callId}.jsonl`));
  const endpoint = await serveHandOff(request.form, request.check);
  try {
    const confined = sandboxed();
    const run = BACKENDS[request.backend](request, endpoint, { confined, started, secret });
    const argv = confined ? confine([request.backend, ...run.args], request) : [request.backend, ...run.args];
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
    const turn = run.finish();
    const result: AgentResult = {
      callId, ...turn, handoff: endpoint.handoff, ms: Date.now() - started,
      // The cause, in order: a stop (cancelled), the backend's own words, its stderr, its exit.
      error: turn.completed ? null : (exit?.stopKind ? exit.error : null) ?? turn.error
        ?? (stderr.join('\n').trim() || `${request.backend} exited with ${exit?.code ?? exit?.signal ?? 'no status'}`),
    };
    mkdirSync(dirname(request.usageFile), { recursive: true });
    appendFileSync(request.usageFile, `${JSON.stringify({
      ts: Date.now() / 1000, callId, role: request.role, backend: request.backend, model: result.model,
      tokens: result.usage && tokens(result.usage), cost_usd: result.usage?.cost_usd ?? null,
    })}\n`);
    return result;
  } finally {
    await endpoint.close();
    rmSync(secret, { force: true });
    await new Promise(resolve => raw.end(resolve));
  }
}

const tokens = ({ cost_usd: _, ...counts }: Usage): Omit<Usage, 'cost_usd'> => counts;
