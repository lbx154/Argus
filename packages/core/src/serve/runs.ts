// The work of each project runs in this process, projects side by side. The operator may write at
// any time: the Manager answers while the team's work goes on. Stopping a project interrupts every
// call under way on it, and its work stays where it was, to be resumed; once nothing is under way,
// its roles' sessions end, and everything they left running.

import type { UploadedAttachment } from '@argus/contracts';
import { Flow } from '../flow.js';
import type { FlowEvent, Project, Question, Store } from '../store.js';
import type { CallAgent } from '../types.js';
import { readRecord, conversationRows, type Live } from './views.js';

/** One frame of a message's stream: `phase` while the Manager works, then `delta`, the reply's row in the record, and `done`; or `error`. */
export type Frame = { type: 'phase' | 'delta' | 'done' | 'error'; [field: string]: unknown };

/** A project's flow while anything is under way on it, and the calls under way. */
interface Active { flow: Flow; controller: AbortController; since: number; calls: Live['calls']; settled: Promise<void> }

/** `changed` is told of each event a project records, and of each change to what this process
 * knows of it with none: its record edited, a message request begun or ended, its run over. */
export class Runs {
  private readonly active = new Map<string, Active>();
  /** Message requests whose Manager turn has not ended, by project. */
  private readonly requests = new Map<string, Set<string>>();

  constructor(private readonly store: Store, readonly callAgent: CallAgent, readonly changed: (id: string, event?: FlowEvent) => void) {}

  live(id: string): Live {
    const active = this.active.get(id);
    return { since: active?.since ?? null, calls: active?.calls ?? [], requests: [...this.requests.get(id) ?? []] };
  }

  /** The answer goes to whoever asked; it throws when the question was answered meanwhile. */
  answer(id: string, question: Question, text: string, attachments?: UploadedAttachment[]): Promise<void> {
    return this.start(id, flow => flow.answer(id, question, text, attachments));
  }

  resume(id: string): void { this.start(id, flow => flow.resume(id)).catch(error => log(id, error)); }

  /** Change the project's record where it is held: by its flow while anything is under way, or on disk. */
  edit(id: string, change: (project: Project) => void): void {
    const active = this.active.get(id);
    if (active) active.flow.edit(id, change);
    else {
      const project = this.store.load(id);
      change(project);
      this.store.save(project);
    }
    this.changed(id);
  }

  /** Stop the work and interrupt every call under way; only the operator starts it again. */
  stop(id: string): void {
    const active = this.active.get(id);
    if (active) {
      active.flow.stop(id);
      active.controller.abort();
    }
    else {
      this.edit(id, project => { if (project.status === 'working') project.status = 'stopped'; });
      void this.callAgent.close?.(`${id}/`);
    }
  }

  /** Interrupt every project's calls, wait until the work has wound down and written its record, and end every session. */
  async stopAll(): Promise<void> {
    const active = [...this.active.values()];
    for (const { controller } of active) controller.abort();
    await Promise.all(active.map(({ settled }) => settled));
    await this.callAgent.close?.('');
  }

  /** The operator's message. Its frames follow the Manager's turn and end with the reply; the
   * team's work it starts, or that was under way, goes on after the stream has ended. */
  async message(id: string, text: string, request: string, send: (frame: Frame) => void, attachments: UploadedAttachment[] = []): Promise<Frame> {
    const requests = this.requests.get(id) ?? new Set<string>();
    this.requests.set(id, requests);
    if (requests.has(request)) return { type: 'error', error: 'This request is already under way.' };
    requests.add(request);
    this.changed(id);
    send({ type: 'phase', role: 'manager', label: 'Manager' });
    try {
      const handoff = await this.start(id, flow => flow.say(id, text, { request_id: request, ...(attachments.length ? { attachments } : {}) }));
      // A Manager turn that failed says why; one a stop cut off ends, its message waiting for Resume.
      if (!handoff) {
        const failed = this.store.load(id).failures?.filter(failure => failure.role === 'manager').at(-1);
        return failed ? { type: 'error', error: failed.error } : { type: 'done', result: { kind: 'stopped', reply: null } };
      }
      // The reply is its row in the record, as the conversation shows it: whom it is to, the message it answers.
      const event = conversationRows(readRecord(this.store, id), this.store.load(id), this.live(id)).find(row => row.type === 'ui.argus' && row.request_id === request && row.live !== true);
      send({ type: 'delta', event });
      return { type: 'done', result: { kind: handoff.to === 'operator' ? 'chat' : 'task', reply: handoff.letters.join('\n\n') } };
    } catch (error) {
      return { type: 'error', error: error instanceof Error ? error.message : String(error) };
    } finally {
      requests.delete(request);
      this.changed(id);
    }
  }

  /** `act` on the project's flow: the one under way, or a new one, once a stopped one has wound down. */
  private async start<T>(id: string, act: (flow: Flow) => T): Promise<T> {
    let active = this.active.get(id);
    while (active?.controller.signal.aborted) {
      await active.settled;
      active = this.active.get(id);
    }
    if (active) return act(active.flow);
    const controller = new AbortController();
    const calls: Live['calls'] = [];
    const flow = new Flow(this.store, this.callAgent, {
      signal: controller.signal,
      onEvent: event => {
        if (event.type === 'call.start') calls.push({ role: event.role, task: event.task, started_ts: Date.now() / 1000, ...event.answers ? { answers: event.answers } : {} });
        else if (event.type === 'call.end') calls.splice(calls.findIndex(call => call.role === event.role && call.task === event.task), 1);
        this.changed(id, event);
      },
    });
    const entry: Active = { flow, controller, since: Date.now() / 1000, calls, settled: Promise.resolve() };
    this.active.set(id, entry);
    try {
      return act(flow);
    } finally {
      entry.settled = flow.settled(id).then(project => project.status, (error: unknown) => log(id, error)).then(status => {
        this.active.delete(id);
        this.changed(id);
        // A project at rest keeps no CLI running, unless a question or a failure waits in a session.
        if (status !== 'asking' && status !== 'failed') return this.callAgent.close?.(`${id}/`);
      });
    }
  }
}

const log = (id: string, error: unknown): void => { process.stderr.write(`${id}: ${error instanceof Error ? error.message : String(error)}\n`); };
