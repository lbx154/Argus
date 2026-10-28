// The flow: every turn ends in a letter and its addressee. The operator writes to the Manager;
// the Manager's brief becomes one task, or goes to the Planner, who hands out one task at a
// time; the Engineer and the Reviewer write to each other about a task. The Planner is called
// when no task is open and something came to it. Where work goes next is the addressee and
// nothing else; the letter is passed on as written. A failed turn is recorded and the flow
// stops, without a retry; a hand-off Argus refuses goes back to its writer once.

import { join } from 'node:path';
import type { Access, AgentResult, CallAgent, Form, HandOff, Role } from './types.js';
import type { FlowEvent, Project, Status, Store, Task, TaskStatus } from './store.js';
import { answered, handedTo, managerPrompt, plannerPrompt, refusedPrompt, taskPrompt } from './prompts.js';

const ROLES: Record<Role, { form: Form; access: Access }> = {
  manager: { form: 'manager', access: 'write' },
  planner: { form: 'plan', access: 'read' },
  engineer: { form: 'engineer', access: 'write' },
  reviewer: { form: 'review', access: 'read' },
};

export interface FlowOptions {
  onEvent?: (event: FlowEvent) => void;
  signal?: AbortSignal;
}

export class Flow {
  constructor(readonly store: Store, private readonly callAgent: CallAgent, private readonly options: FlowOptions = {}) {}

  /** The operator's message: the Manager's turn, then the team's work until it stops. */
  async say(id: string, text: string): Promise<Project> {
    const project = this.store.load(id);
    this.record(project, { type: 'message', from: 'operator', text });
    if (project.status === 'failed') this.status(project, 'working');
    await this.manager(project, text);
    await this.run(project);
    return project;
  }

  /** The operator's answer to the open question goes to whoever asked it. */
  async answer(id: string, text: string): Promise<Project> {
    const project = this.store.load(id);
    const question = project.question;
    if (!question) throw new Error(`${id} has no open question`);
    this.record(project, { type: 'message', from: 'operator', text });
    project.question = null;
    this.status(project, 'working');
    const note = answered(question.question, text);
    if (question.role === 'manager') await this.manager(project, text);
    else if (question.role === 'planner') project.inbox.push(note);
    else Object.assign(this.task(project, question.task), { holder: question.role, note: { from: 'operator', text: note } });
    await this.run(project);
    return project;
  }

  private async run(project: Project): Promise<void> {
    while (project.status === 'working' && !this.options.signal?.aborted) {
      const task = project.tasks.find(task => task.status === 'open');
      if (task) await this.work(project, task);
      else if (complete(project)) this.finish(project);
      else if (project.inbox.length) await this.plan(project);
      else this.status(project, 'idle');
    }
    this.store.save(project);
  }

  private async manager(project: Project, message: string): Promise<void> {
    const handoff = (await this.call(project, 'manager', null,
      resumed => managerPrompt(project, this.store.dir(project.id), message, resumed), checkManager(project)))?.handoff;
    if (!handoff) return;
    if (handoff.to === 'operator') this.ask(project, 'manager', null, handoff.letter);
    else if (handoff.to === 'engineer' || handoff.to === 'planner') this.file(project, handoff.to, handoff.letter);
  }

  /** A ticket, the Manager's brief: one task for the Engineer, or the Planner's to divide. */
  private file(project: Project, to: 'engineer' | 'planner', brief: string): void {
    project.ticket = { brief, task: null };
    if (to === 'planner') project.inbox.push('The Manager filed the ticket above: plan its work.');
    else project.ticket.task = this.add(project, title(brief), '').id;
    this.status(project, 'working');
  }

  private async work(project: Project, task: Task): Promise<void> {
    const role = task.holder;
    const handoff = (await this.call(project, role, task, resumed => taskPrompt(project, task, resumed)))?.handoff;
    if (!handoff) return;
    const { to, letter } = handoff;
    switch (to) {
      case 'engineer': case 'reviewer':
        Object.assign(task, { holder: to, note: { from: role, text: letter } });
        break;
      case 'done':
        this.settle(project, task, 'done', handedTo(role, task, letter));
        break;
      case 'planner':
        this.settle(project, task, 'returned', handedTo(role, task, letter));
        break;
      case 'manager':
        this.settle(project, task, 'returned');
        await this.manager(project, handedTo(role, task, letter));
        break;
      case 'operator':
        this.ask(project, role, task, letter);
    }
  }

  private async plan(project: Project): Promise<void> {
    const handoff = (await this.call(project, 'planner', null, resumed => plannerPrompt(project, resumed)))?.handoff;
    if (!handoff) return;
    project.inbox = [];
    const { to, letter } = handoff;
    if (to === 'engineer') this.add(project, title(letter), letter);
    else if (to === 'done') this.finish(project);
    else if (to === 'operator') this.ask(project, 'planner', null, letter);
    else await this.manager(project, handedTo('planner', null, letter));
  }

  /** One backend turn that ends in an accepted form; null when it failed (recorded, the flow
   * stops) or was interrupted. A refused form goes back once, in the same session, with why. */
  private async call(project: Project, role: Role, task: Task | null, prompt: (resumed: boolean) => string,
                     formCheck?: Check, refused?: string): Promise<AgentResult | null> {
    const key = task ? `${task.id}.${role}` : role;
    const resume = project.sessions[key];
    const where = task ? { task: task.id } : {};
    const dir = this.store.dir(project.id);
    const { signal } = this.options;
    this.record(project, { type: 'call.start', role, ...where });
    let result: AgentResult;
    try {
      result = await this.callAgent({
        backend: project.backend, role, ...ROLES[role], cwd: project.workdir,
        prompt: refused ? refusedPrompt(refused) : prompt(resume !== undefined),
        ...(task ? {} : { readDirs: [dir] }), ...(project.model ? { model: project.model } : {}),
        ...(resume ? { resume } : {}), ...(signal ? { signal } : {}),
        ...(formCheck ? { check: formCheck } : {}), callsDir: join(dir, 'calls'), usageFile: join(dir, 'usage.jsonl'),
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
      this.record(project, { type: 'handoff', role, ...where, to: handoff.to, letter: handoff.letter });
      return result;
    }
    if (completed && threadId && !refused) return this.call(project, role, task, prompt, formCheck, error ?? '');
    if (!signal?.aborted) {
      this.status(project, 'failed', completed ? `The ${role}'s hand-off was refused: ${error}` : error ?? `The ${role}'s turn failed.`);
    }
    return null;
  }

  private add(project: Project, title: string, objective: string): Task {
    const task: Task = { id: `t${project.tasks.length + 1}`, title, objective, status: 'open', holder: 'engineer', note: null };
    project.tasks.push(task);
    this.record(project, { type: 'task.added', task });
    return task;
  }

  /** Close a task; `notice` tells the Planner. */
  private settle(project: Project, task: Task, status: TaskStatus, notice?: string): void {
    Object.assign(task, { status, note: null });
    if (notice) project.inbox.push(notice);
    this.record(project, { type: 'task.settled', task: task.id, status });
  }

  private ask(project: Project, role: Role, task: Task | null, question: string): void {
    project.question = { role, task: task?.id ?? null, question };
    this.status(project, 'asking');
  }

  private finish(project: Project): void {
    project.inbox = [];
    this.status(project, 'done');
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

/** A ticket for the Engineer is complete when its task is accepted and no other task is open. */
function complete({ ticket, tasks }: Project): boolean {
  return !!ticket && tasks.some(task => task.id === ticket.task && task.status === 'done') && tasks.every(task => task.status !== 'open');
}

/** A brief's first line names its task. */
function title(brief: string): string {
  return brief.trim().split('\n')[0]!.slice(0, 100);
}

type Check = (handoff: HandOff) => string | null;

/** The rule the Manager's form cannot say: the team works one ticket at a time. A returned string
 * refuses the hand-off, and the Manager corrects it in one more turn of its session. */
const checkManager = (project: Project): Check => ({ to }) =>
  (to === 'engineer' || to === 'planner') && (project.question || project.tasks.some(task => task.status === 'open'))
    ? 'The team is still on the current ticket: write to=manager now, and hand on new work once that ticket is done.' : null;
