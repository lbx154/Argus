// The flow: every turn ends in a letter to someone. The operator writes to the Manager, who
// answers at once, whether or not the team is working. An untargeted letter to the Engineer opens a task,
// whose brief it is; the Engineer and the Reviewer write to each other about it, and their letter
// to anyone else closes it. Open tasks run side by side, each once the tasks it waits for have
// closed, and the Planner reads its letters as they come, opening several tasks at once if it
// likes. Nothing is done or finished: the work rests when no letter waits for anyone but the
// operator. Where work goes is the addressee and nothing else; the letter is passed on as written.
// A question to the operator, or a failed turn, holds up only its role: the task, or the Planner,
// waits for the operator while the rest of the work goes on. Nothing retries a failed turn; a
// hand-off Argus refuses goes back to its writer once.

import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import type { UploadedAttachment } from '@argus/contracts';
import type { Access, AgentResult, CallAgent, Form, HandOff, Role } from './types.js';
import type { FlowEvent, Note, OperatorMessage, ManagerLetter, Project, Question, Status, Store, Task } from './store.js';
import { answered, handedTo, instructions, letterPrompt, unread, managerPrompt, plannerPrompt, refusedPrompt, SKILLS, skillsHome, taskPrompt } from './prompts.js';
import { keep, titleOf, under } from './serve/files.js';

const TURN: Record<Role, { form: Form; access: Access }> = {
  manager: { form: 'manager', access: 'read' },
  planner: { form: 'plan', access: 'read' },
  engineer: { form: 'engineer', access: 'write' },
  reviewer: { form: 'review', access: 'read' },
};

/** Uploaded files use the same workdir instructions for every recipient. */
const operatorText = ({ text, attachments }: OperatorMessage): string => text + (attachments?.length
  ? `\n\nAttached, in the workdir:\n${attachments.map(file => `- ${file.relative_path}`).join('\n')}` : '');

export interface FlowOptions {
  onEvent?: (event: FlowEvent) => void;
  /** The words a role's call is writing, as far as they have come; none once they are said. */
  onWords?: (role: Role, task: string | undefined, words: string) => void;
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
   * joins the work; like a letter from the team it waits in the record until a turn takes it.
   * Resolves with the Manager's hand-off, or null when the turn failed or was interrupted. */
  say(id: string, text: string, { request_id = randomUUID(), attachments }: Omit<OperatorMessage, 'text'> = {}): Promise<HandOff | null> {
    const project = this.project(id);
    // The message is named: the Manager's turn on it, and the letter that answers it, name it in turn.
    const message: OperatorMessage = { text, request_id, ...attachments?.length ? { attachments } : {} };
    // A message to a stopped project takes the place of the operator's the stop left unanswered, not
    // of one sent since that a Manager turn is taking; the team's letters wait for Resume.
    const taking = this.handing.get(project) ?? [];
    const replaced = project.status === 'stopped' ? (project.forManager ?? []).filter(letter => typeof letter !== 'string' && !taking.includes(letter)) : [];
    if (replaced.length) project.forManager = project.forManager!.filter(letter => !replaced.includes(letter));
    // The project's request is its first message, or the one that took that one's place.
    if (!project.request || replaced.some(letter => typeof letter !== 'string' && letter.request_id === project.requestId)) {
      Object.assign(project, { request: text, requestId: request_id });
    }
    this.record(project, { type: 'message', from: 'operator', ...message });
    if (project.status !== 'stopped') this.retry(project);
    return this.writeToManager(project, message);
  }

  /** The operator's answer to one of the open questions goes to whoever asked it. */
  answer(id: string, asked: Question, text: string, attachments?: UploadedAttachment[]): void {
    const project = this.project(id);
    const at = project.questions.findIndex(({ task, question }) => task === asked.task && question === asked.question);
    if (at < 0) throw new Error(`${id} has no such open question`);
    const [question] = project.questions.splice(at, 1) as [Question];
    const message: OperatorMessage = { text, ...attachments?.length ? { attachments } : {} };
    this.record(project, { type: 'message', from: 'operator', ...message, to: question.role, ...question.task ? { task: question.task } : {} });
    // A stopped project stays stopped: the asker reads the answer when it resumes.
    if (project.status !== 'stopped') {
      this.retry(project);
      this.status(project, 'working');
    }
    const note = answered(question.question, operatorText(message));
    if (question.role === 'planner') project.inbox.push(note);
    else Object.assign(this.task(project, question.task), { holder: question.role, note: { from: 'operator', text: note } });
    this.run(project);
  }

  /** The operator stops the work: no turn starts after those under way. */
  stop(id: string): void {
    const project = this.project(id);
    if (project.status !== 'stopped') this.status(project, 'stopped');
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
    const under = [...this.handing.get(project) ?? []];
    for (const letter of project.forManager ?? []) {
      if (under.includes(letter)) under.splice(under.indexOf(letter), 1);
      else this.handOver(project, letter).catch(error => this.fail(project, 'manager', null, String(error)));
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
    // A turn that throws (the record cannot be written, say) fails its role alone.
    const start = (who: Task | 'planner', go: () => Promise<void>): void => {
      const failed = (error: unknown): void => who === 'planner' ? this.fail(project, 'planner', null, String(error)) : this.fail(project, who.holder, who, String(error));
      running.set(who, go().catch(failed).finally(() => running.delete(who)));
    };
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
  private manager(project: Project, message: ManagerLetter): Promise<HandOff | null> {
    const next = (this.managing.get(project.id) ?? Promise.resolve()).then(() => this.managerTurn(project, message));
    const last: Promise<unknown> = next.catch(() => null).finally(() => {
      if (this.managing.get(project.id) === last) this.managing.delete(project.id);
    });
    this.managing.set(project.id, last);
    return next;
  }

  private async managerTurn(project: Project, letter: ManagerLetter): Promise<HandOff | null> {
    const first = typeof letter !== 'string' && letter.request_id === project.requestId;
    const input = typeof letter === 'string' ? letter : operatorText(letter);
    const answers = typeof letter === 'string' ? undefined : letter.request_id;
    const handoff = (await this.call(project, 'manager', null,
      resumed => managerPrompt(input, resumed), answers ? { answers } : {}))?.handoff ?? null;
    // Only the first request's turn names the project, by the title of the work it hands the team;
    // answered directly, the project keeps its request's first line. Later turns keep that name, even
    // after a session reset.
    const title = !first || !handoff || project.name || handoff.to === 'operator' ? '' : firstLine(read(handoff));
    if (title) project.name = title;
    // A letter to the operator is the Manager's reply; one to the team adds to the work.
    if (handoff && handoff.to !== 'operator') {
      const brief = read(handoff);
      if (handoff.to === 'engineer') {
        const id = handoff.tasks?.[0]?.task;
        if (id) await this.taskLetter(project, 'manager', id, brief);
        else this.open(project, 'manager', brief);
      }
      else project.inbox.push(handedTo('manager', null, brief));
      // New work wakes a resting team; a stopped one takes it up, with what the stop interrupted, on Resume.
      if (project.status !== 'failed' && project.status !== 'stopped') this.status(project, 'working');
    }
    this.run(project);
    return handoff;
  }

  private async work(project: Project, task: Task): Promise<void> {
    const role = task.holder;
    // The letters the turn reads: those on the task as it begins, and those put into it as it goes.
    const taken = new Set(task.unread);
    const handoff = (await this.call(project, role, task, resumed => taskPrompt(project, task, resumed)))?.handoff;
    if (!handoff) return;
    const unread = task.unread?.filter(note => !taken.has(note) && !this.steered.has(note)) ?? [];
    if (unread.length) task.unread = unread;
    else delete task.unread;
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
  private writeToManager(project: Project, letter: ManagerLetter): Promise<HandOff | null> {
    (project.forManager ??= []).push(letter);
    this.store.save(project);
    return this.handOver(project, letter);
  }

  /** The letters put into a turn under way, which its hand-off shows it has read. */
  private readonly steered = new WeakSet<Note>();

  /** Each project's letters a Manager turn is taking or will take, one entry for each. */
  private readonly handing = new Map<Project, Array<ManagerLetter>>();

  private async handOver(project: Project, letter: ManagerLetter): Promise<HandOff | null> {
    const handing = this.handing.get(project) ?? [];
    this.handing.set(project, [...handing, letter]);
    try {
      const handoff = await this.manager(project, letter);
      const at = project.forManager?.indexOf(letter) ?? -1;
      if (handoff && at >= 0) {
        project.forManager!.splice(at, 1);
        this.store.save(project);
      }
      return handoff;
    } finally {
      const now = this.handing.get(project)!;
      now.splice(now.indexOf(letter), 1);
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
    // one naming a task, which its holder reads as soon as they can, or once it has closed, the Planner.
    if (to === 'engineer') {
      const opened = new Map<string, string>();
      for (const [at, letter] of letters.entries()) {
        const { task: id = '', after = [] } = tasks?.[at] ?? {};
        if (!id) opened.set(`t${base + opened.size + 1}`, this.open(project, 'planner', letter, after.map(id => opened.get(id) ?? id)).id);
        else await this.taskLetter(project, 'planner', id, letter);
      }
      this.store.save(project);
    }
    else if (to === 'operator') this.ask(project, 'planner', null, read(handoff));
    else if (to === 'manager') await this.writeToManager(project, handedTo('planner', null, read(handoff)));
  }

  /** A letter stays on its task till a turn that read it ends; a backend that takes one reads it as it goes. */
  private async taskLetter(project: Project, from: Task['from'], id: string, letter: string): Promise<void> {
    const task = openTasks(project).find(task => task.id === id);
    if (!task) return this.returnLetter(project, from, id, letter);
    const note = { from, text: letter };
    (task.unread ??= []).push(note);
    this.store.save(project);
    if (await this.callAgent.steer?.(`${project.id}/${task.id}.${task.holder}`, letterPrompt(from, letter))) this.steered.add(note);
  }

  /** An unread letter goes back to its writer; a Manager's waits until its current turn ends. */
  private returnLetter(project: Project, from: Task['from'], task: string, letter: string): void {
    const note = unread(task, letter);
    if (from === 'manager') void this.writeToManager(project, note).catch(error => this.fail(project, 'manager', null, String(error)));
    else project.inbox.push(note);
  }

  /** One backend turn that ends in an accepted form; null when it failed (recorded, the work
   * stops) or was interrupted. A refused form goes back once, in the same session, with why. */
  private async call(project: Project, role: Role, task: Task | null, prompt: (resumed: boolean) => string,
                     turn: { refused?: string; answers?: string } = {}): Promise<AgentResult | null> {
    const { refused, answers } = turn;
    const answering = answers ? { answers } : {};
    const { signal } = this.options;
    if (signal?.aborted) return null;
    const key = task ? `${task.id}.${role}` : role;
    // Review rounds have no earlier judgment in their context, including after a restart.
    if (role === 'reviewer') delete project.sessions[key];
    const resume = project.sessions[key];
    let stepped = false;
    const where = task ? { task: task.id } : {};
    const dir = this.store.dir(project.id);
    this.record(project, { type: 'call.start', role, ...where, ...answering });
    let result: AgentResult;
    try {
      result = await this.callAgent({
        backend: project.backend, role, ...TURN[role], cwd: project.workdir, instructions: instructions(role, project, this.store),
        prompt: refused ? (role === 'reviewer' ? `${prompt(false)}\n\n` : '') + refusedPrompt(refused) : prompt(resume !== undefined),
        // Every role reads Argus's record of the project and the skills; a role that writes may write skills too.
        readDirs: [dir, SKILLS], writeDirs: [skillsHome(this.store.home)], ...(project.model ? { model: project.model } : {}),
        ...(project.effort ? { effort: project.effort } : {}), ...(resume ? { resume } : {}), ...(signal ? { signal } : {}), session: `${project.id}/${key}`,
        ...(task ? { giver: task.from } : {}), onStep: step => { stepped = true; this.record(project, { type: 'step', role, ...where, ...step }); },
        onWords: words => this.options.onWords?.(role, task?.id, words), callsDir: join(dir, 'calls'), usageFile: join(dir, 'usage.jsonl'),
      });
    } catch (thrown) {
      // The call never ran to an end of its own: it is recorded as ended, failed.
      const error = thrown instanceof Error ? thrown.message : String(thrown);
      this.record(project, { type: 'call.end', role, ...where, callId: '', threadId: null, completed: false, error, model: null, usage: null, ms: 0 });
      if (!signal?.aborted) this.fail(project, role, task, error);
      return null;
    } finally {
      if (role === 'reviewer') await this.callAgent.close?.(`${project.id}/${key}`);
    }
    const { callId, threadId, completed, error, model, usage, ms, handoff } = result;
    // A turn goes on next time in the session its backend reported, whether it ended, was cut off
    // or failed: its work is not done again. With none reported, or a new one whose turn failed before
    // a step, which holds no work and may hold the settings it failed on, the role starts afresh; and
    // a session reset while the turn went on stays reset.
    if (role !== 'reviewer' && project.sessions[key] === resume) {
      if (threadId && (resume || completed || stepped || signal?.aborted)) project.sessions[key] = threadId;
      else delete project.sessions[key];
    }
    if (!project.model && model) {
      project.modelAutomatic ??= true;
      project.model = model;
    }
    this.record(project, { type: 'call.end', role, ...where, callId, threadId, completed, error, model, usage, ms });
    if (handoff) {
      const { to, letters, tasks } = handoff;
      // What a letter hands over is files the operator can open, by their paths in the workspace: one
      // they cannot (hidden, outside it, not there) goes back once, then is left out.
      const found = (handoff.open ?? []).map(path => [path, under(project.workdir, path)?.path] as const);
      const unseen = found.flatMap(([path, seen]) => seen ? [] : [path]);
      if (unseen.length && threadId && !refused) {
        return this.call(project, role, task, prompt, { ...turn, refused: `the operator cannot open ${unseen.join(', ')}: hand over files in the workspace, outside hidden folders` });
      }
      const open = [...new Set(found.flatMap(([, seen]) => seen ?? []))];
      // A result's files are kept as delivered, so that what the next delivery changes can be shown.
      const delivered = open.length ? { open, snapshots: keep(project, dir, open), title: titleOf(project, open[0]!) } : {};
      for (const [at, letter] of letters.entries()) this.record(project, { type: 'handoff', role, ...where,
        ...(tasks?.[at]?.task ? { task: tasks[at]!.task } : {}), to, letter, ...delivered, ...answering });
      return result;
    }
    if (completed && threadId && !refused) return this.call(project, role, task, prompt, { ...turn, refused: error ?? '' });
    if (!signal?.aborted) this.fail(project, role, task, completed ? `The ${role}'s hand-off was refused: ${error}` : error ?? `The ${role}'s turn failed.`);
    return null;
  }

  /** A failed turn holds up its role, on its task if it was on one, and the rest of the work goes
   * on; the project shows it failed once nothing else does, unless the operator stopped it. */
  private fail(project: Project, role: Role, task: Task | null, error: string): void {
    Object.assign(project, { failures: [...project.failures ?? [], { role, task: task?.id ?? null, error }], error });
    if (this.works.has(project.id) || project.status === 'stopped') this.store.save(project);
    else this.status(project, 'failed', error);
  }

  /** A letter to the Engineer opens a task; its first line names it. It waits only for tasks opened
   * before it, so no tasks wait for each other in a circle. */
  private open(project: Project, from: Task['from'], letter: string, after: string[] = []): Task {
    const title = firstLine(letter);
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
    for (const { from, text } of task.unread ?? []) this.returnLetter(project, from, task.id, text);
    delete task.unread;
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

/** A letter's first line as a heading reads it: a link as its words, without markup. It names the
 * task the letter opens. */
export const firstLine = (text: string): string => text.split('\n')
  .map(line => line.replace(/^#+\s*/, '').replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/\*\*|`/g, '').trim()).find(Boolean) ?? '';

/** A hand-off's letters, read as one: there is more than one only when the Planner opens tasks. */
const read = ({ letters }: HandOff): string => letters.join('\n\n');

/** Whether a question or a failed turn holds up the task, or with none, the Planner: it waits for the operator. */
const held = ({ questions, failures = [] }: Project, task: Task | null): boolean =>
  [...questions, ...failures].some(hold => task ? hold.task === task.id : hold.role === 'planner');

/** Whether the task's next turn can start: nothing holds it up, and the tasks it waits for have closed. */
const ready = (project: Project, task: Task): boolean => !held(project, task)
  && (task.after ?? []).every(id => project.tasks.find(other => other.id === id)?.status === 'closed');
