// One backend turn, in its role's session: the session's CLI (under bubblewrap where it starts)
// runs from the session's first turn to its end (session.ts); a turn gives it the prompt, follows
// its events to the turn's end, judges the form the turn ended with, and records the turn's raw
// output and usage.

import { randomBytes } from 'node:crypto';
import { appendFileSync, createWriteStream, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { PayloadSchema } from '@argus/contracts';
import { KEPT, type AgentRequest, type AgentResult, type Backend, type CallAgent, type Step, type Usage } from '../types.js';
import { claude } from './claude.js';
import { codex } from './codex.js';
import { copilot } from './copilot.js';
import { formSchema, judge } from './handoff.js';
import { pi } from './pi.js';
import { agentEnv, confine, launcher, notInstalled, sandboxed } from './sandbox.js';
import { models } from './models.js';
import { Cli } from './session.js';

/** A backend's side of a session: how its CLI starts, and how a turn goes on it. */
export interface Conversation {
  /** The CLI's arguments, for the session's life. */
  args: string[];
  /** Settings the CLI takes only from its environment. */
  env?: Record<string, string>;
  /** The CLI takes the request's instructions itself. */
  instructed?: boolean;
  /** One turn: the prompt in, how the turn ended out, each step told as it comes. The first turn
   * on a CLI readies it for the session first, with the request that started it; `request` is
   * this turn's. */
  turn(cli: Cli, prompt: string, turn: { request: AgentRequest; onStep: (step: Step | null) => void; started: number }): Promise<TurnEnd>;
}

/** How a turn ended; `output` is its final answer, the form as filled in. */
export type TurnEnd = Pick<AgentResult, 'threadId' | 'completed' | 'error' | 'model' | 'usage'> & { output: unknown };

export type StartBackend = (
  request: AgentRequest,
  call: { confined: boolean; schema: PayloadSchema; /** calls/<call>, the stem of any file the session needs. */ stem: string },
) => Conversation;

const START: Record<Backend, StartBackend> = { codex, claude, copilot, pi };

/** A session's CLI, the settings it runs with, and the backend session it holds. */
interface Live { cli: Cli; conversation: Conversation; settings: string; thread: string | null }
/** Every CLI running, by its session's key, or for a call with none, by the call's. */
const live = new Map<string, Live>();

/** What a CLI is started with: a turn that asks for others starts a new one. Codex takes a model and
 * an effort with each turn. */
const settings = ({ backend, cwd, access, readDirs, writeDirs, model, effort }: AgentRequest): string =>
  JSON.stringify([backend, cwd, access, readDirs, writeDirs, ...backend === 'codex' ? [] : [model, effort]]);

/** The session's CLI as it runs, or a new one, which resumes the session from the backend's record. */
async function session(key: string, request: AgentRequest, stem: string): Promise<Live> {
  const kept = live.get(key);
  if (kept && !kept.cli.gone && kept.settings === settings(request) && kept.thread === (request.resume ?? null)) return kept;
  await end(key);
  const confined = sandboxed();
  const conversation = START[request.backend](request, { confined, schema: formSchema(request.form, request.giver), stem });
  const argv = confined ? confine([request.backend, ...conversation.args], request) : [...launcher(request.backend), ...conversation.args];
  const started: Live = {
    cli: new Cli(argv, request.cwd, { ...agentEnv(request.backend), ...conversation.env }),
    conversation, settings: settings(request), thread: request.resume ?? null,
  };
  live.set(key, started);
  return started;
}

/** Ends a CLI, the one kept under `key` unless another is named, and all it started. */
async function end(key: string, cli = live.get(key)?.cli): Promise<void> {
  if (live.get(key)?.cli === cli) live.delete(key);
  await cli?.close();
}

export const callAgent: CallAgent = Object.assign(async (request: AgentRequest): Promise<AgentResult> => {
  const started = Date.now();
  const callId = `${new Date(started).toISOString().replace(/\D/g, '').slice(0, 14)}-${randomBytes(3).toString('hex')}`;
  mkdirSync(request.callsDir, { recursive: true });
  const stem = join(request.callsDir, callId);
  const raw = createWriteStream(`${stem}.jsonl`);
  const key = request.session ?? `call/${callId}`;
  try {
    const kept = await session(key, request, stem);
    const { cli, conversation } = kept;
    const { instructions } = request;
    const prompt = conversation.instructed || request.resume || !instructions ? request.prompt : `${instructions}\n\n${request.prompt}`;
    // What the role was told, as sent: a turn can be read, and replayed, from the record.
    writeFileSync(`${stem}.prompt.md`, conversation.instructed && instructions ? `${instructions}\n\n${prompt}` : prompt);
    // A stop ends the session: the turn, and all its CLI started.
    const stop = (): void => { void end(key, cli); };
    request.signal?.addEventListener('abort', stop, { once: true });
    if (request.signal?.aborted) stop();
    cli.raw = line => raw.write(`${line}\n`);
    cli.stderr.length = 0;
    let turn: TurnEnd;
    let broke = false;
    try {
      turn = await conversation.turn(cli, prompt, { request, onStep: step => { if (step) request.onStep?.(step); }, started });
    } catch (thrown) {
      broke = true;
      turn = { threadId: kept.thread, completed: false, error: thrown instanceof Error ? thrown.message : String(thrown), output: undefined, model: null, usage: null };
    } finally {
      cli.raw = null;
      request.signal?.removeEventListener('abort', stop);
    }
    kept.thread = turn.threadId;
    // A CLI stays for its session's next turn only where its backend keeps one, its turn was not
    // stopped and it is still in step; otherwise it ends now, with all it started. A turn the
    // backend failed leaves it running.
    if (!KEPT.includes(request.backend) || request.session === undefined || cli.gone || broke || request.signal?.aborted) await end(key, cli);
    const { output, ...ended } = turn;
    const schema = formSchema(request.form, request.giver);
    const verdict = ended.completed ? judge(request.form, schema, output) : null;
    const result: AgentResult = {
      callId, ...ended, handoff: typeof verdict === 'string' ? null : verdict, ms: Date.now() - started,
      // A refused form says why; a failed turn its cause, in order: a CLI not installed, a stop,
      // the backend's own words, the CLI's.
      error: typeof verdict === 'string' ? verdict : ended.completed ? null
        : notInstalled(request.backend) ?? (request.signal?.aborted ? 'Run cancelled.' : null) ?? ended.error ?? cli.why(),
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
}, {
  /** Ends every CLI kept under a key that starts with `prefix`, and all they started. */
  close: (prefix: string): Promise<void> => Promise.all([...live.keys()].filter(key => key.startsWith(prefix)).map(key => end(key))).then(() => undefined),
  models,
});

const tokens = ({ cost_usd: _, ...counts }: Usage): Omit<Usage, 'cost_usd'> => counts;
