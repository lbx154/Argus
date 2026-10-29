// A role's session on its backend, kept open between its turns: one CLI, started at the session's
// first turn and given each turn's prompt as it comes, so that what a turn leaves running, a
// training job say, runs on into the next, and the backend keeps what it has read. The session
// ends, and everything its CLI started with it, when its task closes, its project stops or Argus
// exits; a CLI that exits on its own, or a turn that is stopped, ends it too. The next turn then
// starts a new CLI, which resumes the session from the backend's own record.

import { isJsonObject } from '@argus/contracts';
import { executeProcess } from '@argus/runtime';

export type Message = Record<string, unknown>;

/** A running CLI that speaks JSON lines on stdin and stdout, JSON-RPC among them. */
export class Cli {
  private readonly queued: string[] = [];
  private wake = (): void => {};
  private closed = false;
  private readonly stop = new AbortController();
  private listener: ((message: Message) => void) | null = null;
  private readonly replies = new Map<number, (reply: Message) => void>();
  private ids = 0;
  /** Its last lines on stderr, which say why it failed. */
  readonly stderr: string[] = [];
  /** Why it is gone, once the CLI itself has exited. */
  gone: string | null = null;
  private readonly exited: Promise<void>;
  /** Everything it started has ended too. */
  readonly ended: Promise<void>;
  /** Where each line it writes goes: the raw output of the turn under way. */
  raw: ((line: string) => void) | null = null;

  constructor(argv: string[], cwd: string, env: Record<string, string>, private readonly header: Message = {}) {
    let exit = (): void => {};
    this.exited = new Promise(resolve => { exit = resolve; });
    const input = async function* (this: Cli): AsyncGenerator<string> {
      for (;;) {
        while (this.queued.length) yield this.queued.shift()!;
        if (this.closed) return;
        await new Promise<void>(resolve => { this.wake = resolve; });
      }
    }.call(this);
    this.ended = (async () => {
      for await (const item of executeProcess({
        executable: argv[0]!, args: argv.slice(1), cwd, env, input, signal: this.stop.signal,
        onExit: (code, signal) => { this.gone ??= `exited with ${code ?? signal}`; exit(); },
      })) {
        if (item.type === 'exit') { this.gone ??= item.error ?? `exited with ${item.code ?? item.signal}`; continue; }
        this.raw?.(item.stream === 'stdout' ? item.line : JSON.stringify({ stderr: item.line }));
        if (item.stream === 'stderr') { if (this.stderr.push(item.line) > 20) this.stderr.shift(); continue; }
        let message: unknown;
        try { message = JSON.parse(item.line); } catch { continue; }
        if (isJsonObject(message)) this.take(message);
      }
      this.gone ??= 'exited';
      exit();
    })();
  }

  private take(message: Message): void {
    const { id, method } = message;
    if (typeof id === 'number' && method === undefined && this.replies.has(id)) {
      this.replies.get(id)!(message);
      this.replies.delete(id);
    } else if (id !== undefined && typeof method === 'string') {
      // A request of the CLI's own, such as for an approval: Argus grants nothing it was not started with.
      this.send({ id, error: { code: -32601, message: `Argus does not answer ${method}.` } });
    } else this.listener?.(message);
  }

  send(message: Message): void {
    if (this.closed) return;
    this.queued.push(`${JSON.stringify({ ...this.header, ...message })}\n`);
    this.wake();
  }

  /** A JSON-RPC request: its result, or an error with the CLI's words. */
  async request(method: string, params: unknown): Promise<Message> {
    const id = ++this.ids;
    const reply = new Promise<Message>(resolve => this.replies.set(id, resolve));
    this.send({ id, method, params });
    const answer = await Promise.race([reply, this.exited.then(() => null)]);
    if (!answer) throw new Error(this.why());
    if (isJsonObject(answer.error)) throw new Error(`${method}: ${String(answer.error.message ?? JSON.stringify(answer.error))}`);
    return isJsonObject(answer.result) ? answer.result : {};
  }

  /** Hands each message the CLI writes to `take` until it says the turn has ended (true), or the CLI exits (false). */
  until(take: (message: Message) => boolean): Promise<boolean> {
    return new Promise((resolve, reject) => {
      // A `take` that throws (the step cannot be recorded, say) ends the turn with its error.
      this.listener = message => {
        try { if (take(message)) { this.listener = null; resolve(true); } } catch (error) { this.listener = null; reject(error); }
      };
      void this.exited.then(() => resolve(false));
    });
  }

  /** For a CLI that takes one prompt and exits: `text` is its whole input, and each message it
   * writes goes to `take` as `until` hands them. */
  once(text: string, take: (message: Message) => boolean): Promise<boolean> {
    const ended = this.until(take);
    this.queued.push(text);
    this.closed = true;
    this.wake();
    return ended;
  }

  /** Why the CLI is gone, in its own words where it left some. */
  why(): string {
    return this.stderr.join('\n').trim() || `The backend CLI ${this.gone ?? 'stopped'}.`;
  }

  /** Ends the CLI and everything it started; resolves once all of it has ended. */
  close(): Promise<void> {
    this.closed = true;
    this.wake();
    this.stop.abort();
    return this.ended;
  }
}
