// The flow: the operator's message goes to the Manager; a ticket becomes tasks the Engineer and
// the Reviewer hand between them; the Planner is called when no task is ready and something
// came to it. Where work goes next is the hand-off's `to` and nothing else. A failed turn is
// recorded and the flow stops, without a retry; a form Argus refuses goes back to the role once.

import { join } from 'node:path';
import type { Access, AgentResult, CallAgent, Form, HandOff, Role } from './types.js';
import type { FlowEvent, Project, Question, Status, Store, Task, TaskStatus, Ticket } from './store.js';
import { answered, handedTo, managerPrompt, plannerPrompt, refusedPrompt, taskPrompt } from './prompts.js';

const ROLES: Record<Role, { form: Form; access: Access }> = {
  manager: { form: 'manager', access: 'write' },
  planner: { form: 'plan', access: 'read' },
  engineer: { form: 'engineer', access: 'write' },
  reviewer: { form: 'review', access: 'read' },
};

/** The fields of a `plan` form's task the flow keeps; the rest of the brief goes into its objective. */
interface PlannedTask {
  title: string; objective: string; key?: string; deps?: string[]; non_goals?: string[];
  require_independent_review?: boolean; [field: string]: unknown;
}
const BRIEF: [string, string][] = [
  ['hypothesis', 'Hypothesis'], ['decision_rule', 'Decision rule'], ['acceptance_check', 'Check'], ['expected_regressions', 'Expected regressions'],
];

export interface FlowOptions {
  onEvent?: (event: FlowEvent) => void;
  signal?: AbortSignal;
}

export class Flow {
  constructor(readonly store: Store, private readonly callAgent: CallAgent, private readonly options: FlowOptions = {}) {}

  /** The operator's message: the Manager's turn, then the team's work until it stops. */
  async say(id: string, text: string): Promise<Project> {
    const project = this.store.load(id);
    project.request ||= text;
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

  /** Carry on where the work stopped: after an interruption, or a failed call the operator retries. */
  async resume(id: string): Promise<Project> {
    const project = this.store.load(id);
    if (project.status === 'failed') this.status(project, 'working');
    await this.run(project);
    return project;
  }

  private async run(project: Project): Promise<void> {
    while (project.status === 'working' && !this.options.signal?.aborted) {
      const task = nextTask(project);
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
    const reply = String(handoff.fields.reply ?? '').trim();
    if (reply) this.record(project, { type: 'message', from: 'manager', text: reply });
    if (handoff.to === 'operator') this.ask(project, 'manager', null, handoff);
    else if (handoff.to === 'team') this.file(project, handoff);
  }

  /** A ticket: a direct one becomes one task; a staged one goes to the Planner. */
  private file(project: Project, { fields }: HandOff): void {
    const plan = fields.plan as { path: string } | undefined;
    const goal = String(fields.goal);
    const ticket: Ticket = {
      goal, acceptance: list(fields.acceptance), constraints: list(fields.constraints),
      context: [...list(fields.context), ...(plan ? [plan.path] : [])], standing: fields.lifetime === 'standing', task: null,
    };
    project.ticket = ticket;
    if (fields.workflow_mode === 'staged') project.inbox.push('The Manager filed the ticket above: plan its work.');
    else {
      const review = fields.require_independent_review !== false;
      ticket.task = this.add(project, { title: goal.split('\n')[0]!.slice(0, 100), objective: '', deps: [], review }).id;
    }
    this.status(project, 'working');
  }

  private async work(project: Project, task: Task): Promise<void> {
    const role = task.holder;
    const check = role === 'engineer' ? checkEngineer(task) : undefined;
    const handoff = (await this.call(project, role, task, resumed => taskPrompt(project, task, resumed), check))?.handoff;
    if (!handoff) return;
    const note = String(handoff.fields.note ?? '');
    switch (handoff.to) {
      case 'engineer': case 'reviewer':
        Object.assign(task, { holder: handoff.to, note: { from: role, text: note } });
        break;
      case 'done':
        this.settle(project, task, 'done', `Task ${task.id} is done: ${handoff.summary}`);
        break;
      case 'planner':
        this.settle(project, task, 'returned', handedTo(role, task, handoff.summary, note));
        break;
      case 'manager':
        this.settle(project, task, 'returned');
        await this.manager(project, handedTo(role, task, handoff.summary, note));
        break;
      case 'operator':
        this.ask(project, role, task, handoff);
    }
  }

  private async plan(project: Project): Promise<void> {
    const handoff = (await this.call(project, 'planner', null, resumed => plannerPrompt(project, resumed), checkPlan(project)))?.handoff;
    if (!handoff) return;
    project.inbox = [];
    for (const { id } of list<{ id: string }>(handoff.fields.retire)) this.settle(project, this.task(project, id), 'retired');
    if (handoff.to === 'engineer') {
      const ids = new Map<string, string>();
      for (const spec of list<PlannedTask>(handoff.fields.tasks)) {
        const objective = [spec.objective, ...BRIEF.filter(([field]) => spec[field]).map(([field, label]) => `${label}: ${String(spec[field])}`),
          spec.non_goals?.length && `Not in scope: ${spec.non_goals.join('; ')}`].filter(Boolean).join('\n');
        const deps = (spec.deps ?? []).map(key => ids.get(key)!);
        const task = this.add(project, { title: spec.title, objective, deps, review: spec.require_independent_review !== false });
        if (spec.key) ids.set(spec.key, task.id);
      }
    } else if (handoff.to === 'done') this.finish(project);
    else if (handoff.to === 'operator') this.ask(project, 'planner', null, handoff);
    else await this.manager(project, handedTo('planner', null, handoff.summary, ''));
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
      this.record(project, { type: 'handoff', role, ...where, to: handoff.to, summary: handoff.summary, fields: handoff.fields });
      return result;
    }
    if (completed && threadId && !refused) return this.call(project, role, task, prompt, formCheck, error ?? '');
    if (!signal?.aborted) {
      this.status(project, 'failed', completed ? `The ${role}'s hand-off was refused: ${error}` : error ?? `The ${role}'s turn failed.`);
    }
    return null;
  }

  private add(project: Project, spec: Pick<Task, 'title' | 'objective' | 'deps' | 'review'>): Task {
    const task: Task = { id: `t${project.tasks.length + 1}`, ...spec, status: 'open', holder: 'engineer', note: null };
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

  private ask(project: Project, role: Role, task: Task | null, { fields }: HandOff): void {
    const { question, options } = fields.ask as Pick<Question, 'question' | 'options'>;
    project.question = { role, task: task?.id ?? null, question, options };
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

/** The open task whose dependencies are done: the one worked next. */
export function nextTask({ tasks }: Project): Task | undefined {
  return tasks.find(task => task.status === 'open' && task.deps.every(dep => tasks.find(other => other.id === dep)?.status === 'done'));
}

/** A bounded direct ticket is complete when its task is accepted and no other task is open. */
function complete({ ticket, tasks }: Project): boolean {
  return !!ticket && !ticket.standing && tasks.some(task => task.id === ticket.task && task.status === 'done')
    && tasks.every(task => task.status !== 'open');
}

function list<T = string>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

// The forms' own rules, beyond what their schema can say: a returned string refuses the
// hand-off, and the role corrects it in one more turn of its session.

type Check = (handoff: HandOff) => string | null;

/** A ticket is the team's whole brief, and one is worked at a time. */
const checkManager = (project: Project): Check => ({ to, fields }) => {
  if (to === 'team' ? !('goal' in fields && 'acceptance' in fields) : 'goal' in fields || 'acceptance' in fields) {
    return 'to=team goes with goal and acceptance, and they only with to=team.';
  }
  return to === 'team' && (project.question || project.tasks.some(task => task.status === 'open'))
    ? 'The team is still on the current ticket: answer with to=manager now, and file new work once that ticket is done.' : null;
};

const checkEngineer = (task: Task): Check => ({ to }) =>
  to === 'done' && task.review ? 'This task requires an independent review: hand it to=reviewer.' : null;

const checkPlan = (project: Project): Check => ({ to, fields }) => {
  const tasks = list<PlannedTask>(fields.tasks);
  if (tasks.length && to !== 'engineer') return 'tasks start only with to=engineer.';
  if (to === 'done' && project.ticket?.standing) return 'This ticket is ongoing and never done: start the next task, or ask the operator.';
  const keys = new Set<string>();
  for (const task of tasks) {
    const unknown = task.deps?.find(dep => !keys.has(dep));
    if (unknown !== undefined) return `tasks.deps of "${task.title}": ${unknown} is not the key of a task listed before it in this call.`;
    if (task.key !== undefined && keys.has(task.key)) return `tasks.key: ${task.key} names two tasks.`;
    if (task.key !== undefined) keys.add(task.key);
  }
  const closed = list<{ id: string }>(fields.retire).find(({ id }) => project.tasks.find(task => task.id === id)?.status !== 'open');
  return closed ? `retire: ${closed.id} is not an open task.` : null;
};
