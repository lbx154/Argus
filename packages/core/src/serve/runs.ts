// The work of each project runs in this process, one Flow call at a time in the order asked
// for; projects run side by side. Stopping a project interrupts the call under way.

import { Flow } from '../flow.js';
import type { FlowEvent, Store } from '../store.js';
import type { CallAgent, Role } from '../types.js';
import { readEvents, type Live } from './views.js';

/** One frame of a message's stream: `phase` while the Manager works, then `delta`, `done` or `error`. */
export type Frame = { type: 'phase' | 'delta' | 'done' | 'error'; [field: string]: unknown };

interface Job {
  run(flow: Flow, signal: AbortSignal): Promise<unknown>;
  onEvent?: (event: FlowEvent) => void;
}

interface Run { job: Job; controller: AbortController; since: number; role: Role | null }

/** `changed` is told of each event a project records. */
export class Runs {
  private readonly queues = new Map<string, Job[]>();
  private readonly running = new Map<string, Run>();
  /** Message requests whose Manager turn has not ended, by project; the value cancels one. */
  private readonly requests = new Map<string, Map<string, () => void>>();

  constructor(private readonly store: Store, private readonly callAgent: CallAgent, private readonly changed: (id: string) => void) {}

  live(id: string): Live {
    const run = this.running.get(id);
    return { since: run?.since ?? null, role: run?.role ?? null, requests: [...this.requests.get(id)?.keys() ?? []] };
  }

  answer(id: string, text: string): void { this.enqueue(id, { run: flow => flow.answer(id, text) }); }

  resume(id: string): void { this.enqueue(id, { run: flow => flow.resume(id) }); }

  /** Interrupt the call under way; the work stays where it was, to be resumed. */
  stop(id: string): void { this.running.get(id)?.controller.abort(); }

  stopAll(): void { for (const run of this.running.values()) run.controller.abort(); }

  cancel(id: string, request: string): boolean {
    const cancel = this.requests.get(id)?.get(request);
    cancel?.();
    return cancel !== undefined;
  }

  /** The operator's message. Its frames follow the Manager's turn and end with the reply;
   * the team's work it starts goes on after the stream has ended. */
  message(id: string, text: string, request: string, send: (frame: Frame) => void): Promise<Frame> {
    const requests = this.requests.get(id) ?? new Map<string, () => void>();
    this.requests.set(id, requests);
    if (requests.has(request)) return Promise.resolve({ type: 'error', error: 'This request is already under way.' });
    return new Promise<Frame>(settle => {
      let cancelled = false;
      let answered = false;
      let reply = '';
      let replyId = '';
      let kind = 'chat';
      const end = (frame: Frame): void => {
        if (requests.get(request) !== cancel) return;
        requests.delete(request);
        if (frame.type === 'done' && reply) send({ type: 'delta', text: reply, message_id: replyId, fragment_mode: 'snapshot' });
        settle(frame);
      };
      const done = (): void => end({ type: 'done', result: cancelled ? { kind: 'cancelled', reply: null } : { kind, reply: reply || null } });
      const job: Job = {
        run: async (flow, signal) => {
          if (cancelled) return done();
          try {
            await flow.say(id, text);
          } catch (error) {
            end({ type: 'error', error: error instanceof Error ? error.message : String(error) });
          }
          cancelled ||= signal.aborted;
          done();
        },
        onEvent: event => {
          if (event.type === 'message' && event.from === 'operator') replyId = `m${readEvents(this.store, id).length - 1}-argus`;
          else if (event.type === 'call.start' && !answered) send({ type: 'phase', role: 'manager', label: 'Manager' });
          else if (event.type === 'call.start') done();
          else if (event.type === 'handoff' && event.role === 'manager') { answered = true; reply = event.letter; }
          else if (event.type === 'task.added') kind = 'task';
          else if (event.type === 'status' && event.status === 'asking') kind = 'pending_question';
          else if (event.type === 'status' && event.status === 'failed') end({ type: 'error', error: event.error ?? 'The Manager did not answer.' });
        },
      };
      const cancel = (): void => {
        cancelled = true;
        if (this.running.get(id)?.job === job) this.stop(id);
        done();
      };
      requests.set(request, cancel);
      if (this.running.has(id)) send({ type: 'phase', role: 'manager', label: 'Waiting for the work under way to stop' });
      this.enqueue(id, job);
    });
  }

  private enqueue(id: string, job: Job): void {
    this.queues.set(id, [...this.queues.get(id) ?? [], job]);
    if (!this.running.has(id)) void this.next(id);
  }

  private async next(id: string): Promise<void> {
    const job = this.queues.get(id)?.shift();
    if (!job) return void this.queues.delete(id);
    const run: Run = { job, controller: new AbortController(), since: Date.now() / 1000, role: null };
    this.running.set(id, run);
    const flow = new Flow(this.store, this.callAgent, {
      signal: run.controller.signal,
      onEvent: event => {
        if (event.type === 'call.start') run.role = event.role;
        else if (event.type === 'call.end') run.role = null;
        job.onEvent?.(event);
        this.changed(id);
      },
    });
    try {
      await job.run(flow, run.controller.signal);
    } catch (error) {
      process.stderr.write(`${id}: ${error instanceof Error ? error.message : String(error)}\n`);
    } finally {
      this.running.delete(id);
      void this.next(id);
    }
  }
}
