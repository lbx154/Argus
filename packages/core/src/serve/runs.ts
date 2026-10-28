// The work of each project runs in this process, projects side by side. The operator may write at
// any time: the Manager answers while the team's work goes on. Stopping a project interrupts every
// call under way on it, and its work stays where it was, to be resumed.

import { Flow } from '../flow.js';
import type { Project, Store } from '../store.js';
import type { CallAgent, Role } from '../types.js';
import { readEvents, webEvents, type Live } from './views.js';

/** One frame of a message's stream: `phase` while the Manager works, then `delta`, `done` or `error`. */
export type Frame = { type: 'phase' | 'delta' | 'done' | 'error'; [field: string]: unknown };

/** A project's flow while anything is under way on it, and the calls under way. */
interface Active { flow: Flow; controller: AbortController; since: number; roles: Role[]; settled: Promise<void> }

/** `changed` is told of each event a project records. */
export class Runs {
  private readonly active = new Map<string, Active>();
  /** Message requests whose Manager turn has not ended, by project; the value cancels one. */
  private readonly requests = new Map<string, Map<string, () => void>>();

  constructor(private readonly store: Store, private readonly callAgent: CallAgent, private readonly changed: (id: string) => void) {}

  live(id: string): Live {
    const active = this.active.get(id);
    return { since: active?.since ?? null, roles: active?.roles ?? [], requests: [...this.requests.get(id)?.keys() ?? []] };
  }

  answer(id: string, text: string): void { void this.start(id, flow => flow.answer(id, text)); }

  resume(id: string): void { void this.start(id, flow => flow.resume(id)); }

  /** Change the project's record where it is held: by its flow while anything is under way, or on disk. */
  edit(id: string, change: (project: Project) => void): void {
    const active = this.active.get(id);
    if (active) return active.flow.edit(id, change);
    const project = this.store.load(id);
    change(project);
    this.store.save(project);
  }

  stop(id: string): void { this.active.get(id)?.controller.abort(); }

  stopAll(): void { for (const active of this.active.values()) active.controller.abort(); }

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
        this.changed(id);
      },
    });
    const entry: Active = { flow, controller, since: Date.now() / 1000, roles, settled: Promise.resolve() };
    this.active.set(id, entry);
    try {
      return act(flow);
    } finally {
      entry.settled = flow.settled(id).then(() => undefined, (error: unknown) => {
        process.stderr.write(`${id}: ${error instanceof Error ? error.message : String(error)}\n`);
      }).then(() => {
        this.active.delete(id);
        this.changed(id);
      });
    }
  }
}
