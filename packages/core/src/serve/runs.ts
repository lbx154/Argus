// The work of each project runs in this process, projects side by side. The operator may write at
// any time: the Manager answers while the team's work goes on. Stopping a project interrupts every
// call under way on it, and its work stays where it was, to be resumed; once nothing is under way,
// its roles' sessions end, and everything they left running.

import { Flow } from '../flow.js';
import type { FlowEvent, Project, Question, Store } from '../store.js';
import type { CallAgent, Role } from '../types.js';
import { readEvents, webEvents, type Live } from './views.js';

/** One frame of a message's stream: `phase` while the Manager works, then `delta`, `done` or `error`. */
export type Frame = { type: 'phase' | 'delta' | 'done' | 'error'; [field: string]: unknown };

/** A project's flow while anything is under way on it, and the calls under way. */
interface Active { flow: Flow; controller: AbortController; since: number; roles: Role[]; settled: Promise<void> }

/** `changed` is told of each event a project records, and of each change to what this process
 * knows of it with none: its record edited, a message request begun or ended, its run over. */
export class Runs {
  private readonly active = new Map<string, Active>();
  /** Message requests whose Manager turn has not ended, by project; the value cancels one. */
  private readonly requests = new Map<string, Map<string, () => void>>();

  constructor(private readonly store: Store, readonly callAgent: CallAgent, readonly changed: (id: string, event?: FlowEvent) => void) {}

  live(id: string): Live {
    const active = this.active.get(id);
    return { since: active?.since ?? null, roles: active?.roles ?? [], requests: [...this.requests.get(id)?.keys() ?? []] };
  }

  /** The answer goes to whoever asked; it throws when the question was answered meanwhile. */
  answer(id: string, question: Question, text: string): Promise<void> { return this.start(id, flow => flow.answer(id, question, text)); }

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

  /** Stop the work, recorded so that nothing but the operator starts it again: the calls under way
   * end, or with `now` are interrupted. Whether any was under way. */
  stop(id: string, now: boolean): boolean {
    const active = this.active.get(id);
    if (!active) {
      this.edit(id, project => { if (project.status === 'working') project.status = 'stopped'; });
      void this.callAgent.close?.(`${id}/`);
    }
    active?.flow.stop(id);
    if (now) active?.controller.abort();
    return active !== undefined;
  }

  /** Interrupt every project's calls, wait until the work has wound down and written its record, and end every session. */
  async stopAll(): Promise<void> {
    const active = [...this.active.values()];
    for (const { controller } of active) controller.abort();
    await Promise.all(active.map(({ settled }) => settled));
    await this.callAgent.close?.('');
  }

  cancel(id: string, request: string): boolean {
    const cancel = this.requests.get(id)?.get(request);
    cancel?.();
    return cancel !== undefined;
  }

  /** The operator's message. Its frames follow the Manager's turn and end with the reply; the
   * team's work it starts, or that was under way, goes on after the stream has ended. */
  async message(id: string, text: string, request: string, send: (frame: Frame) => void): Promise<Frame> {
    const requests = this.requests.get(id) ?? new Map<string, () => void>();
    this.requests.set(id, requests);
    if (requests.has(request)) return { type: 'error', error: 'This request is already under way.' };
    const controller = new AbortController();
    requests.set(request, () => controller.abort());
    this.changed(id);
    send({ type: 'phase', role: 'manager', label: 'Manager' });
    try {
      const handoff = await this.start(id, flow => flow.say(id, text, controller.signal));
      if (controller.signal.aborted) return { type: 'done', result: { kind: 'cancelled', reply: null } };
      if (!handoff) return { type: 'error', error: this.store.load(id).error ?? 'The Manager did not answer.' };
      const reply = handoff.letters.join('\n\n');
      // The reply's id is the one its row in the record has: it names the message it answers.
      const row = webEvents(readEvents(this.store, id)).filter(event => event.type === 'ui.argus').at(-1);
      send({ type: 'delta', text: reply, message_id: row?.message_id ?? '', fragment_mode: 'snapshot' });
      return { type: 'done', result: { kind: handoff.to === 'operator' ? 'chat' : 'task', reply } };
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
    const roles: Role[] = [];
    const flow = new Flow(this.store, this.callAgent, {
      signal: controller.signal,
      onEvent: event => {
        if (event.type === 'call.start') roles.push(event.role);
        else if (event.type === 'call.end') roles.splice(roles.indexOf(event.role), 1);
        this.changed(id, event);
      },
    });
    const entry: Active = { flow, controller, since: Date.now() / 1000, roles, settled: Promise.resolve() };
    this.active.set(id, entry);
    try {
      return act(flow);
    } finally {
      entry.settled = flow.settled(id).then(project => project.status, (error: unknown) => log(id, error)).then(status => {
        this.active.delete(id);
        if (status === 'stopped') void this.callAgent.close?.(`${id}/`);
        this.changed(id);
      });
    }
  }
}

const log = (id: string, error: unknown): void => { process.stderr.write(`${id}: ${error instanceof Error ? error.message : String(error)}\n`); };
