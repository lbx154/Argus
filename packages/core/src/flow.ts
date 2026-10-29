// The flow: every turn ends in a letter to someone. The operator writes to the Manager, who
// answers at once, whether or not the team is working. A letter to the Engineer opens a task,
// whose brief it is; the Engineer and the Reviewer write to each other about it, and their letter
// to anyone else closes it. Open tasks run side by side, each once the tasks it waits for have
// closed, and the Planner reads its letters as they come, opening several tasks at once if it
// likes. Nothing is done or finished: the work rests when no letter waits for anyone but the
// operator. Where work goes is the addressee and nothing else; the letter is passed on as written.
// A question to the operator, or a failed turn, holds up only its role: the task, or the Planner,
// waits for the operator while the rest of the work goes on. Nothing retries a failed turn; a
// hand-off Argus refuses goes back to its writer once.

import { join } from 'node:path';
import type { Access, AgentResult, CallAgent, Form, HandOff, Role } from './types.js';
import type { FlowEvent, Project, Question, Status, Store, Task } from './store.js';
import { answered, handedTo, instructions, letterPrompt, managerPrompt, plannerPrompt, refusedPrompt, SKILLS, skillsHome, taskPrompt } from './prompts.js';
import { keep } from './serve/files.js';

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
   * joins the work; like a letter from the team it waits in the record until a turn takes it, or
   * `signal` withdraws it. Resolves with the Manager's hand-off, or null when the turn failed or
   * was interrupted. */
  say(id: string, text: string, signal?: AbortSignal): Promise<HandOff | null> {
    const project = this.project(id);
    project.request ||= text;
    this.retry(project);
    return this.writeToManager(project, text, { signal, operator: true });
  }

  /** The operator's answer to one of the open questions goes to whoever asked it. */
  answer(id: string, asked: Question, text: string): void {
    const project = this.project(id);
    const at = project.questions.findIndex(({ task, question }) => task === asked.task && question === asked.question);
    if (at < 0) throw new Error(`${id} has no such open question`);
    const [question] = project.questions.splice(at, 1) as [Question];
    this.record(project, { type: 'message', from: 'operator', text, to: question.role });
    this.retry(project);
    this.status(project, 'working');
    const note = answered(question.question, text);
    if (question.role === 'planner') project.inbox.push(note);
    else Object.assign(this.task(project, question.task), { holder: question.role, note: { from: 'operator', text: note } });
    this.run(project);
  }

  /** The operator stops the work: no turn starts after those under way. */
  stop(id: string): void {
    const project = this.project(id);
    if (project.status === 'working') this.status(project, 'stopped');
  }

  /** Carry on where the work stopped: after the operator stopped it, an interruption, or a failed call the operator retries. */
  resume(id: string): void {
    const project = this.project(id);
    if (project.status === 'stopped') this.status(project, 'working');
    this.retry(project);
    this.run(project);
  }

  /** After a failed call the operator writes or resumes: the failed turns go on, and the letters a
   * failed or interrupted Manager turn left unread go to the Manager again. */
  private retry(project: Project): void {
    if (project.failures?.length) Object.assign(project, { failures: [], error: null });
    if (project.status === 'failed') this.status(project, 'working');
    for (const letter of project.forManager ?? []) {
      if (!this.handing.has(letter)) this.handOver(project, letter).catch(error => this.fail(project, 'manager', null, String(error)));
    }
  }

  /** A change to the project's record from outside its turns, such as its name, kept with the rest. */
  edit(id: string, change: (project: Project) => void): void {
    const project = this.project(id);
    change(project);
    this.store.save(project);
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

  /** Every open task goes on in its own turns once the tasks it waits for have closed, and the
   * Planner reads its letters as they come, all side by side; what is held up waits for the operator. */
  private async carryOn(project: Project, work: { wake: () => void }): Promise<void> {
    const running = new Map<Task | 'planner', Promise<void>>();
    const start = (who: Task | 'planner', go: () => Promise<void>): void => { running.set(who, go().finally(() => running.delete(who))); };
    for (;;) {
      if (this.going(project)) {
        for (const task of openTasks(project)) if (!running.has(task) && ready(project, task)) start(task, () => this.carry(project, task));
        if (!running.has('planner') && project.inbox.length && !held(project, null)) start('planner', () => this.plan(project));
      }
      if (!running.size) break;
      await Promise.race([...running.values(), new Promise<void>(resolve => { work.wake = resolve; })]);
    }
    // At rest, what still waits is for the operator: a failed turn to resume, or a question.
    if (this.going(project)) {
      const failed = Boolean(project.failures?.length);
      this.status(project, failed ? 'failed' : project.questions.length ? 'asking' : 'idle', failed ? project.error : null);
    }
    this.store.save(project);
  }

  private going(project: Project): boolean {
    return project.status === 'working' && !this.options.signal?.aborted;
  }

  private async carry(project: Project, task: Task): Promise<void> {
    while (task.status === 'open' && ready(project, task) && this.going(project)) await this.work(project, task);
  }

  /** The Manager's turns on a project come one after another, in the order their letters came. */
  private manager(project: Project, message: string, turn: ManagerTurn = {}): Promise<HandOff | null> {
    const next = (this.managing.get(project.id) ?? Promise.resolve()).then(() => this.managerTurn(project, message, turn));
    const last: Promise<unknown> = next.catch(() => null).finally(() => {
      if (this.managing.get(project.id) === last) this.managing.delete(project.id);
    });
    this.managing.set(project.id, last);
    return next;
  }

  private async managerTurn(project: Project, message: string, { signal, operator }: ManagerTurn): Promise<HandOff | null> {
    if (signal?.aborted) return null;
    // The operator's message is recorded when the Manager takes it up, so the letter that follows is its reply.
    if (operator) this.record(project, { type: 'message', from: 'operator', text: message });
    const handoff = (await this.call(project, 'manager', null,
      resumed => managerPrompt(message, resumed), { signal }))?.handoff ?? null;
    // A letter to the operator is the Manager's reply; one to the team adds to the work.
    if (handoff && handoff.to !== 'operator') {
      // The project's request is the message that first set the team working; till then, the first one.
      if (operator && !begun(project)) project.request = message;
      const letter = read(handoff);
      if (handoff.to === 'engineer') this.open(project, 'manager', letter);
      else project.inbox.push(handedTo('manager', null, letter));
      // New work wakes a resting team; a stopped one only when the operator asked for it.
      if (project.status !== 'failed' && (operator || project.status !== 'stopped')) this.status(project, 'working');
    }
    this.run(project);
    return handoff;
  }

  private async work(project: Project, task: Task): Promise<void> {
    const role = task.holder;
    // Letters that reach the task during the turn wait for the next.
    const taken = task.unread?.length ?? 0;
    const handoff = (await this.call(project, role, task, resumed => taskPrompt(project, task, resumed)))?.handoff;
    if (!handoff) return;
    task.unread?.splice(0, taken);
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
        await this.writeToManager(project, handedTo(role, task, letter));
        break;
      case 'operator':
        this.ask(project, role, task, letter);
    }
  }

  /** A letter to the Manager stays in the record until a Manager turn takes it. */
  private writeToManager(project: Project, letter: string, turn: ManagerTurn = {}): Promise<HandOff | null> {
    (project.forManager ??= []).push(letter);
    this.store.save(project);
    return this.handOver(project, letter, turn);
  }

  private readonly handing = new Set<string>();

  private async handOver(project: Project, letter: string, turn: ManagerTurn = {}): Promise<HandOff | null> {
    this.handing.add(letter);
    try {
      const handoff = await this.manager(project, letter, turn);
      if (handoff || turn.signal?.aborted) {
        project.forManager?.splice(project.forManager.indexOf(letter), 1);
        this.store.save(project);
      }
      return handoff;
    } finally {
      this.handing.delete(letter);
    }
  }

  private async plan(project: Project): Promise<void> {
    // Letters that reach the Planner during its turn wait for its next one.
    const taken = project.inbox.length;
    // The ids the Planner was told its tasks take, though the Manager may open tasks meanwhile.
    const base = project.tasks.length;
    const handoff = (await this.call(project, 'planner', null, () => plannerPrompt(project)))?.handoff;
    if (!handoff) return;
    project.inbox.splice(0, taken);
    const { to, letters, tasks } = handoff;
    // A letter to itself opens nothing: the Planner reads on when the next letter comes; nor does
    // one to an open task, which its holder reads as soon as they can.
    if (to === 'engineer') {
      const opened = new Map<string, string>();
      for (const [at, letter] of letters.entries()) {
        const { task: id = '', after = [] } = tasks?.[at] ?? {};
        const open = openTasks(project).find(task => task.id === id);
        if (!open) opened.set(`t${base + opened.size + 1}`, this.open(project, 'planner', letter, after.map(id => opened.get(id) ?? id)).id);
        // In the holder's turn under way, where its backend can take a letter; else at their next.
        else if (!await this.callAgent.steer?.(`${project.id}/${open.id}.${open.holder}`, letterPrompt('planner', letter))) {
          (open.unread ??= []).push({ from: 'planner', text: letter });
        }
      }
      this.store.save(project);
    }
    else if (to === 'operator') this.ask(project, 'planner', null, read(handoff));
    else if (to === 'manager') await this.writeToManager(project, handedTo('planner', null, read(handoff)));
  }

  /** One backend turn that ends in an accepted form; null when it failed (recorded, the work
   * stops) or was interrupted. A refused form goes back once, in the same session, with why. */
  private async call(project: Project, role: Role, task: Task | null, prompt: (resumed: boolean) => string,
                     turn: { signal?: AbortSignal | undefined; refused?: string } = {}): Promise<AgentResult | null> {
    const { refused } = turn;
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
        backend: project.backend, role, ...TURN[role], cwd: project.workdir, instructions: instructions(role, TURN[role].access === 'write', project, this.store),
        prompt: refused ? refusedPrompt(refused) : prompt(resume !== undefined),
        // Every role reads Argus's record of the project and the skills; a role that writes may write skills too.
        readDirs: [dir, SKILLS], writeDirs: [skillsHome(this.store.home)], ...(project.model ? { model: project.model } : {}),
        ...(project.effort ? { effort: project.effort } : {}), ...(resume ? { resume } : {}), ...(signal ? { signal } : {}), session: `${project.id}/${key}`,
        ...(task ? { giver: task.from } : {}), onStep: step => this.record(project, { type: 'step', role, ...where, ...step }), callsDir: join(dir, 'calls'), usageFile: join(dir, 'usage.jsonl'),
      });
    } catch (thrown) {
      // The call never ran to an end of its own: it is recorded as ended, failed.
      const error = thrown instanceof Error ? thrown.message : String(thrown);
      this.record(project, { type: 'call.end', role, ...where, callId: '', threadId: null, completed: false, error, model: null, usage: null, ms: 0 });
      if (!signal?.aborted) this.fail(project, role, task, error);
      return null;
    }
    const { callId, threadId, completed, error, model, usage, ms, handoff } = result;
    // A turn goes on next time in the session its backend reported, whether it ended, was cut off
    // or failed: its work is not done again. With none reported, the role starts afresh; and a
    // session reset while the turn went on stays reset.
    if (project.sessions[key] === resume) {
      if (threadId) project.sessions[key] = threadId;
      else delete project.sessions[key];
    }
    this.record(project, { type: 'call.end', role, ...where, callId, threadId, completed, error, model, usage, ms });
    if (handoff) {
      const { to, letters, open } = handoff;
      // A result's files are kept as delivered, so that what the next delivery changes can be shown.
      const delivered = open ? { open, snapshots: keep(project, dir, open) } : {};
      for (const letter of letters) this.record(project, { type: 'handoff', role, ...where, to, letter, ...delivered });
      return result;
    }
    if (completed && threadId && !refused) return this.call(project, role, task, prompt, { ...turn, refused: error ?? '' });
    if (!signal?.aborted) this.fail(project, role, task, completed ? `The ${role}'s hand-off was refused: ${error}` : error ?? `The ${role}'s turn failed.`);
    return null;
  }

  /** A failed turn holds up its role, on its task if it was on one, and the rest of the work goes
   * on; the project shows it failed once nothing else does. */
  private fail(project: Project, role: Role, task: Task | null, error: string): void {
    Object.assign(project, { failures: [...project.failures ?? [], { role, task: task?.id ?? null, error }], error });
    if (this.works.has(project.id)) this.store.save(project);
    else this.status(project, 'failed', error);
  }

  /** A letter to the Engineer opens a task; its first line names it. It waits only for tasks opened
   * before it, so no tasks wait for each other in a circle. */
  private open(project: Project, from: Task['from'], letter: string, after: string[] = []): Task {
    const title = letter.split('\n')[0]!.slice(0, 100);
    const waits = [...new Set(after)].filter(id => project.tasks.some(task => task.id === id));
    const task: Task = {
      id: `t${project.tasks.length + 1}`, title, from, objective: letter, status: 'open', holder: 'engineer', note: null, ...waits.length ? { after: waits } : {},
    };
    project.tasks.push(task);
    this.record(project, { type: 'task.added', task });
    return task;
  }

  /** A letter out of the task closes it, and stays on it as its last word; its sessions end, and
   * everything they left running. */
  private close(project: Project, task: Task, from: Role, letter: string): void {
    Object.assign(task, { status: 'closed', note: { from, text: letter } });
    this.record(project, { type: 'task.closed', task: task.id });
    void this.callAgent.close?.(`${project.id}/${task.id}.`);
  }

  /** The question's letter is in the record already; its asker waits for the answer. */
  private ask(project: Project, role: Role, task: Task | null, question: string): void {
    project.questions.push({ role, task: task?.id ?? null, question });
    this.store.save(project);
  }

  private status(project: Project, status: Status, error: string | null = null): void {
    if (project.status === status && project.error === error) return;
    Object.assign(project, { status, error });
    this.record(project, { type: 'status', status, ...(error ? { error } : {}) });
  }

  private task(project: Project, id: string | null): Task {
    const task = project.tasks.find(task => task.id === id);
    if (!task) throw new Error(`${project.id} has no task ${id}`);
    return task;
  }

  /** State first, then the event that records the change; a step changes none. */
  private record(project: Project, event: FlowEvent): void {
    if (event.type !== 'step') this.store.save(project);
    this.store.append(project.id, event);
    this.options.onEvent?.(event);
  }
}

export const openTasks = ({ tasks }: Project): Task[] => tasks.filter(task => task.status === 'open');

/** A hand-off's letters, read as one: there is more than one only when the Planner opens tasks. */
const read = ({ letters }: HandOff): string => letters.join('\n\n');

/** A Manager turn on the operator's message, which `signal` withdraws, or on a letter from the team. */
type ManagerTurn = { signal?: AbortSignal | undefined; operator?: boolean };

/** Whether the team has had work: a task, or a letter to the Planner. */
const begun = ({ tasks, inbox, sessions }: Project): boolean => tasks.length > 0 || inbox.length > 0 || 'planner' in sessions;

/** Whether a question or a failed turn holds up the task, or with none, the Planner: it waits for the operator. */
const held = ({ questions, failures = [] }: Project, task: Task | null): boolean =>
  [...questions, ...failures].some(hold => task ? hold.task === task.id : hold.role === 'planner');

/** Whether the task's next turn can start: nothing holds it up, and the tasks it waits for have closed. */
const ready = (project: Project, task: Task): boolean => !held(project, task)
  && (task.after ?? []).every(id => project.tasks.find(other => other.id === id)?.status === 'closed');
