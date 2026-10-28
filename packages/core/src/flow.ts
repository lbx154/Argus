// The flow: every turn ends in a letter to someone. The operator writes to the Manager, who
// answers at once, whether or not the team is working. A letter to the Engineer opens a task,
// whose brief it is; the Engineer and the Reviewer write to each other about it, and their letter
// to anyone else closes it. Open tasks run side by side. The Planner reads its letters when no
// task is open, and may open several at once. Nothing is done or finished: the work rests when no
// letter waits for anyone but the operator. Where work goes is the addressee and nothing else; the
// letter is passed on as written. A failed turn is recorded and the work stops, without a retry;
// a hand-off Argus refuses goes back to its writer once.

import { join } from 'node:path';
import type { Access, AgentResult, CallAgent, Form, HandOff, Role } from './types.js';
import type { FlowEvent, Project, Status, Store, Task } from './store.js';
import { answered, handedTo, managerPrompt, plannerPrompt, refusedPrompt, taskPrompt, wayRoots } from './prompts.js';

const TURN: Record<Role, { form: Form; access: Access }> = {
  manager: { form: 'manager', access: 'write' },
  planner: { form: 'plan', access: 'read' },
  engineer: { form: 'engineer', access: 'write' },
  reviewer: { form: 'review', access: 'read' },
};

export interface FlowOptions {
  onEvent?: (event: FlowEvent) => void;
  /** Interrupts every call under way, and ends the work. */
  signal?: AbortSignal;
}

export class Flow {
  /** Each project this flow has opened: one object, which every turn on it shares. */
  private readonly projects = new Map<string, Project>();
  /** Each project's work under way, and how to wake it when a Manager turn adds to it. */
  private readonly works = new Map<string, { done: Promise<void>; wake: () => void }>();
  /** Each project's last Manager turn, which its next one follows. */
  private readonly managing = new Map<string, Promise<unknown>>();

  constructor(readonly store: Store, private readonly callAgent: CallAgent, private readonly options: FlowOptions = {}) {}

  /** The operator's message. The Manager reads it in its next turn, and what its letter hands on
   * joins the work. Resolves with the Manager's hand-off, or null when the turn failed or `signal`
   * interrupted it. */
  say(id: string, text: string, signal?: AbortSignal): Promise<HandOff | null> {
    const project = this.project(id);
    project.request ||= text;
    if (project.status === 'failed') this.status(project, 'working');
    return this.manager(project, text, { signal, operator: true });
  }

  /** The operator's answer to the open question goes to whoever asked it. */
  answer(id: string, text: string): void {
    const project = this.project(id);
    const question = project.question;
    if (!question) throw new Error(`${id} has no open question`);
    this.record(project, { type: 'message', from: 'operator', text });
    project.question = null;
    this.status(project, 'working');
    const note = answered(question.question, text);
    if (question.role === 'planner') project.inbox.push(note);
    else Object.assign(this.task(project, question.task), { holder: question.role, note: { from: 'operator', text: note } });
    this.run(project);
  }

  /** Carry on where the work stopped: after an interruption, or a failed call the operator retries. */
  resume(id: string): void {
    const project = this.project(id);
    if (project.status === 'failed') this.status(project, 'working');
    this.run(project);
  }

  /** The project once nothing is under way on it: no Manager turn, no work. */
  async settled(id: string): Promise<Project> {
    for (let busy = this.busy(id); busy.length; busy = this.busy(id)) await Promise.all(busy);
    return this.project(id);
  }

  private busy(id: string): Promise<unknown>[] {
    return [this.managing.get(id), this.works.get(id)?.done].filter(promise => promise !== undefined);
  }

  private project(id: string): Project {
    const project = this.projects.get(id) ?? this.store.load(id);
    this.projects.set(id, project);
    return project;
  }

  /** Start the project's work, or wake the work under way to take up what a Manager turn added. */
  private run(project: Project): void {
    const under = this.works.get(project.id);
    if (under) return under.wake();
    const work = { done: Promise.resolve(), wake: () => {} };
    this.works.set(project.id, work);
    work.done = this.carryOn(project, work).finally(() => this.works.delete(project.id));
  }

  /** Every open task goes on in its own turns, one opened meanwhile at once; with none open, the
   * Planner reads its letters. */
  private async carryOn(project: Project, work: { wake: () => void }): Promise<void> {
    const running = new Map<Task, Promise<void>>();
    for (;;) {
      if (this.going(project)) {
        for (const task of openTasks(project)) {
          if (!running.has(task)) running.set(task, this.carry(project, task).finally(() => running.delete(task)));
        }
      }
      if (running.size) await Promise.race([...running.values(), new Promise<void>(resolve => { work.wake = resolve; })]);
      else if (!this.going(project)) break;
      else if (project.inbox.length) await this.plan(project);
      else this.status(project, 'idle');
    }
    this.store.save(project);
  }

  private going(project: Project): boolean {
    return project.status === 'working' && !this.options.signal?.aborted;
  }

  private async carry(project: Project, task: Task): Promise<void> {
    while (task.status === 'open' && this.going(project)) await this.work(project, task);
  }

  /** The Manager's turns on a project come one after another, in the order their letters came. */
  private manager(project: Project, message: string, turn: { signal?: AbortSignal | undefined; operator?: boolean } = {}): Promise<HandOff | null> {
    const next = (this.managing.get(project.id) ?? Promise.resolve()).then(() => this.managerTurn(project, message, turn));
    const last: Promise<unknown> = next.catch(() => null).finally(() => {
      if (this.managing.get(project.id) === last) this.managing.delete(project.id);
    });
    this.managing.set(project.id, last);
    return next;
  }

  private async managerTurn(project: Project, message: string, { signal, operator }: { signal?: AbortSignal | undefined; operator?: boolean }):
    Promise<HandOff | null> {
    if (signal?.aborted) return null;
    // The operator's message is recorded when the Manager takes it up, so the letter that follows is its reply.
    if (operator) this.record(project, { type: 'message', from: 'operator', text: message });
    const handoff = (await this.call(project, 'manager', null,
      resumed => managerPrompt(project, this.store.dir(project.id), wayRoots(this.store.home), message, resumed), { signal }))?.handoff ?? null;
    // A letter to the operator is the Manager's reply; one to the team adds to the work.
    if (handoff && handoff.to !== 'operator') {
      const letter = read(handoff);
      if (handoff.to === 'engineer') this.open(project, 'manager', letter);
      else project.inbox.push(handedTo('manager', null, letter));
      if (!project.question) this.status(project, 'working');
    }
    this.run(project);
    return handoff;
  }

  private async work(project: Project, task: Task): Promise<void> {
    const role = task.holder;
    const handoff = (await this.call(project, role, task, resumed => taskPrompt(project, this.store.dir(project.id), task, resumed),
      { check: oneQuestion(project) }))?.handoff;
    if (!handoff) return;
    const { to } = handoff;
    const letter = read(handoff);
    switch (to) {
      case 'engineer': case 'reviewer':
        Object.assign(task, { holder: to, note: { from: role, text: letter } });
        break;
      case 'planner':
        this.close(project, task, role, letter);
        project.inbox.push(handedTo(role, task, letter));
        break;
      case 'manager':
        this.close(project, task, role, letter);
        await this.manager(project, handedTo(role, task, letter));
        break;
      case 'operator':
        this.ask(project, role, task, letter);
    }
  }

  private async plan(project: Project): Promise<void> {
    // Letters that reach the Planner during its turn wait for its next one.
    const taken = project.inbox.length;
    const handoff = (await this.call(project, 'planner', null, resumed => plannerPrompt(project, this.store.dir(project.id), resumed)))?.handoff;
    if (!handoff) return;
    project.inbox.splice(0, taken);
    const { to, letters } = handoff;
    if (to === 'engineer') for (const letter of letters) this.open(project, 'planner', letter);
    else if (to === 'operator') this.ask(project, 'planner', null, read(handoff));
    else await this.manager(project, handedTo('planner', null, read(handoff)));
  }

  /** One backend turn that ends in an accepted form; null when it failed (recorded, the work
   * stops) or was interrupted. A refused form goes back once, in the same session, with why. */
  private async call(project: Project, role: Role, task: Task | null, prompt: (resumed: boolean) => string,
                     turn: { check?: Check; signal?: AbortSignal | undefined; refused?: string } = {}): Promise<AgentResult | null> {
    const { check, refused } = turn;
    const signals = [this.options.signal, turn.signal].filter(signal => signal !== undefined);
    const signal = signals.length ? AbortSignal.any(signals) : undefined;
    if (signal?.aborted) return null;
    const key = task ? `${task.id}.${role}` : role;
    const resume = project.sessions[key];
    const where = task ? { task: task.id } : {};
    const dir = this.store.dir(project.id);
    this.record(project, { type: 'call.start', role, ...where });
    let result: AgentResult;
    try {
      result = await this.callAgent({
        backend: project.backend, role, ...TURN[role], cwd: project.workdir,
        prompt: refused ? refusedPrompt(refused) : prompt(resume !== undefined),
        // Every role reads Argus's record of the project and the ways of working.
        readDirs: [dir, ...wayRoots(this.store.home)], ...(project.model ? { model: project.model } : {}),
        ...(project.effort ? { effort: project.effort } : {}), ...(resume ? { resume } : {}), ...(signal ? { signal } : {}),
        ...(check ? { check } : {}), callsDir: join(dir, 'calls'), usageFile: join(dir, 'usage.jsonl'),
      });
    } catch (error) {
      if (!signal?.aborted) this.status(project, 'failed', error instanceof Error ? error.message : String(error));
      return null;
    }
    const { callId, threadId, completed, error, model, usage, ms, handoff } = result;
    // Only a completed turn is continued; after a failed or interrupted one the role starts afresh.
    if (completed && threadId) project.sessions[key] = threadId;
    this.record(project, { type: 'call.end', role, ...where, callId, threadId, completed, error, model, usage, ms });
    if (handoff) {
      for (const letter of handoff.letters) this.record(project, { type: 'handoff', role, ...where, to: handoff.to, letter });
      return result;
    }
    if (completed && threadId && !refused) return this.call(project, role, task, prompt, { ...turn, refused: error ?? '' });
    if (!signal?.aborted) {
      this.status(project, 'failed', completed ? `The ${role}'s hand-off was refused: ${error}` : error ?? `The ${role}'s turn failed.`);
    }
    return null;
  }

  /** A letter to the Engineer opens a task; its first line names it. */
  private open(project: Project, from: Task['from'], letter: string): void {
    const title = letter.split('\n')[0]!.slice(0, 100);
    const task: Task = { id: `t${project.tasks.length + 1}`, title, from, objective: letter, status: 'open', holder: 'engineer', note: null };
    project.tasks.push(task);
    this.record(project, { type: 'task.added', task });
  }

  /** A letter out of the task closes it, and stays on it as its last word. */
  private close(project: Project, task: Task, from: Role, letter: string): void {
    Object.assign(task, { status: 'closed', note: { from, text: letter } });
    this.record(project, { type: 'task.closed', task: task.id });
  }

  private ask(project: Project, role: Role, task: Task | null, question: string): void {
    project.question = { role, task: task?.id ?? null, question };
    this.status(project, 'asking');
  }

  private status(project: Project, status: Status, error: string | null = null): void {
    if (project.status === status && project.error === error) return;
    Object.assign(project, { status, error });
    this.record(project, {
      type: 'status', status, ...(error ? { error } : {}), ...(status === 'asking' && project.question ? { question: project.question } : {}),
    });
  }

  private task(project: Project, id: string | null): Task {
    const task = project.tasks.find(task => task.id === id);
    if (!task) throw new Error(`${project.id} has no task ${id}`);
    return task;
  }

  /** State first, then the event that records the change. */
  private record(project: Project, event: FlowEvent): void {
    this.store.save(project);
    this.store.append(project.id, event);
    this.options.onEvent?.(event);
  }
}

export const openTasks = ({ tasks }: Project): Task[] => tasks.filter(task => task.status === 'open');

/** A hand-off's letters, read as one: there is more than one only when the Planner opens tasks. */
const read = ({ letters }: HandOff): string => letters.join('\n\n');

type Check = (handoff: HandOff) => string | null;

/** The rule a task's form cannot say: the operator answers one question at a time. A returned
 * string refuses the hand-off, and its writer corrects it in one more turn of its session. */
const oneQuestion = (project: Project): Check => ({ to }) => to === 'operator' && project.question
  ? `The operator has yet to answer another question: carry on without them, or write to someone else.` : null;
