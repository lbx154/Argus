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

import { createHash, randomUUID } from 'node:crypto';
import { join, delimiter } from 'node:path';
import type { UploadedAttachment } from '@argus/contracts';
import type { Access, AgentResult, CallAgent, Form, HandOff, Role } from './types.js';
import { effortOf, modelOf, type FlowEvent, type Note, type OperatorMessage, type ManagerLetter, type Project, type Question, type Status, type Store, type Task } from './store.js';
import { answered, copied, NAMES, heldLetter, startedAfresh, steeredPrompt, handedTo, posted, reinstructed, instructions, lookPrompt, letterPrompt, unread, managerPrompt, packsHome, plannerPrompt, refusedPrompt, SKILLS, skillsHome, toolsHome, wikiHome, taskPrompt } from './prompts.js';
import { keep, titleOf, under } from './serve/files.js';
import { nameLegacyInputs } from './inputs.js';
import { forgetWorktree, freeze, isCheckout, makeWorktree } from './worktrees.js';
import { Progress } from './progress.js';
import { letterbox } from './letters.js';
import { keepLibrary } from './libraryHistory.js';
import { packEnv, prepare } from './packSetup.js';

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
  onWords?: (role: Role, task: string | undefined, words: string, kind?: 'process' | 'answer') => void;
  /** Interrupts every call under way, and ends the work. */
  signal?: AbortSignal;
  /** With no one watching, a failed turn must not wait unseen: it goes on by itself after this long,
   * in its session and then in a new one, and if it still fails the Manager hears of it, to tell the operator. Unset, it just holds. */
  retryFailedAfterMs?: number;
}

/** Waits, or stops waiting when the work is interrupted. */
const pause = (ms: number, signal?: AbortSignal): Promise<void> => new Promise(resolve => {
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true });
});

export class Flow {
  /** Each project this flow has opened: one object, which every turn on it shares. */
  private readonly projects = new Map<string, Project>();
  /** Each project's work under way, and how to wake it when a Manager turn adds to it. */
  private readonly works = new Map<string, { done: Promise<void>; wake: () => void }>();
  /** Reviewers' looks at work under way, which the work waits for before it rests. */
  private readonly looks = new Map<string, Set<Promise<void>>>();
  /** Each project's last Manager turn, which its next one follows. */
  private readonly managing = new Map<string, Promise<unknown>>();

  constructor(readonly store: Store, private readonly callAgent: CallAgent, private readonly options: FlowOptions = {}) {
    this.progress = new Progress(store);
  }

  /** PROGRESS.md, where the work stands, rebuilt from the record. */
  private readonly progress: Progress;

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
    const replaced = project.status === 'stopped' ? (project.forManager ?? []).filter(letter => typeof letter !== 'string' && !letter.input_id && !taking.includes(letter)) : [];
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
    // Pending team letters from older records acquire their identity before being called.
    if (project.forManager?.some(letter => typeof letter === 'string')) {
      project.forManager = nameLegacyInputs(this.store, project);
      this.store.save(project);
    }
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
      const looks = this.looks.get(project.id) ?? new Set<Promise<void>>();
      if (!running.size && !looks.size) break;
      await Promise.race([...running.values(), ...looks, new Promise<void>(resolve => { work.wake = resolve; })]);
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
    this.place(project, task);
    while (task.status === 'open' && ready(project, task) && this.going(project)) await this.work(project, task);
  }

  /** As a task begins it works in the workspace, unless another open task already does; then, in a
   * git checkout, in a worktree of its own. Elsewhere tasks share the workspace, as before. */
  private place(project: Project, task: Task): void {
    if (task.place) return;
    const shared = project.tasks.some(other => other !== task && other.status === 'open' && other.place === 'workspace');
    if (shared && isCheckout(project.workdir)) {
      try {
        task.place = makeWorktree(project.workdir, task.id);
        this.record(project, { type: 'task.worktree', task: task.id, dir: task.place.dir, base: task.place.base });
      } catch (error) {
        task.place = 'workspace';
        this.record(project, { type: 'task.worktree', task: task.id, error: String(error) });
      }
    } else task.place = 'workspace';
    // Where the task's changes are counted from, for its Reviewer.
    if (isCheckout(project.workdir)) {
      try { task.frozen = { start: typeof task.place === 'object' ? task.place.base : freeze(project.workdir, `Argus: the workspace as task ${task.id} began`) }; }
      catch { /* a checkout git cannot read: the Reviewer reads the files as they are */ }
    }
    this.store.save(project);
  }

  /** The Manager's turns on a project come one after another, in the order their letters came. */
  private manager(project: Project, message: ManagerLetter): Promise<HandOff | null> {
    const next = (this.managing.get(project.id) ?? Promise.resolve()).then(() => {
      // A letter an earlier turn already read is answered by that turn.
      const read = typeof message === 'string' ? undefined : this.readBy.get(message);
      if (read) return read;
      // The team's letters waiting when the turn begins are read together, and answered once.
      const also = typeof message === 'string' || !message.input_id ? [] : (project.forManager ?? []).filter((other): other is Exclude<ManagerLetter, string> =>
        other !== message && typeof other !== 'string' && Boolean(other.input_id) && !this.readBy.has(other));
      const turn = this.managerTurn(project, message, also);
      for (const other of also) this.readBy.set(other, turn);
      this.managerTurns.set(project.id, turn);
      void turn.finally(() => { if (this.managerTurns.get(project.id) === turn) this.managerTurns.delete(project.id); }).catch(() => {});
      return turn;
    });
    const last: Promise<unknown> = next.catch(() => null).finally(() => {
      if (this.managing.get(project.id) === last) this.managing.delete(project.id);
    });
    this.managing.set(project.id, last);
    return next;
  }

  /** The Manager's turn under way on each project. */
  private readonly managerTurns = new Map<string, Promise<HandOff | null>>();

  /** Team letters a Manager turn read beside the one it was started for, and that turn's answer. */
  private readonly readBy = new WeakMap<object, Promise<HandOff | null>>();

  private async managerTurn(project: Project, letter: ManagerLetter, also: Exclude<ManagerLetter, string>[] = []): Promise<HandOff | null> {
    const first = typeof letter !== 'string' && !letter.input_id && letter.request_id === project.requestId;
    const input = [typeof letter === 'string' ? letter : operatorText(letter), ...also.map(operatorText)].join('\n\n');
    const answers = typeof letter === 'string' ? undefined : letter.request_id;
    const input_id = typeof letter === 'string' ? undefined : letter.input_id;
    const handoff = (await this.call(project, 'manager', null,
      resumed => managerPrompt(input, resumed), { ...answers ? { answers } : {}, ...input_id ? { input_id } : {} }))?.handoff ?? null;
    // Only the first request's turn names the project, by the title of the work it hands the team;
    // answered directly, the project keeps its request's first line. Later turns keep that name, even
    // after a session reset.
    const name = !first || !handoff || project.name || handoff.to === 'operator' ? '' : title(read(handoff));
    if (name) project.name = name;
    // A letter to the operator is the Manager's reply; one to the team adds to the work; one to itself goes nowhere.
    if (handoff && handoff.to !== 'operator' && handoff.to !== 'manager') {
      const brief = read(handoff) + (typeof letter === 'string' || letter.input_id ? '' : `\n\n## Operator message\n${input}`);
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
    const turn = () => this.call(project, role, task, resumed => taskPrompt(project, task, resumed));
    const handoff = (await (role === 'reviewer' ? this.reviewTurn(task, turn) : turn()))?.handoff;
    if (!handoff) return;
    const unread = task.unread?.filter(note => !taken.has(note) && !this.steered.has(note)) ?? [];
    if (unread.length) task.unread = unread;
    else delete task.unread;
    const { to } = handoff;
    const letter = read(handoff);
    // Letters to the tasks beside it reach their holders as they work.
    for (const { task: id, letter: text } of handoff.tell ?? []) if (id !== task.id) await this.taskLetter(project, role, id, text);
    if (handoff.look) this.lookAt(project, task, handoff.look);
    switch (to) {
      case 'engineer': case 'reviewer':
        // The candidate the Reviewer judges is frozen by Argus, not described by its producer.
        if (to === 'reviewer' && role === 'engineer' && task.frozen) {
          try {
            const at = freeze(typeof task.place === 'object' ? task.place.dir : project.workdir, `Argus: task ${task.id} as handed to its Reviewer`);
            task.frozen = { start: task.frozen.start, before: task.frozen.last ?? task.frozen.start, last: at };
          } catch { /* the Reviewer reads the files as they are */ }
        }
        Object.assign(task, { holder: to, note: { from: role, text: letter } });
        // Beside other tasks, the Planner reads each hand-over in one it gave as the work goes, and may
        // steer this task or those beside it: with its next letter, since a hand-over inside a task
        // changes no plan. What does (a task that closes, a letter written to it or copied to it) wakes it.
        if (to !== role && task.from === 'planner' && openTasks(project).some(other => other !== task)) {
          (project.beside ??= []).push(copied(role, to, task, letter));
        }
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

  /** The Reviewer looks at a task's work under way, beside its Engineer's turns; the letter goes to the task's holder. */
  private lookAt(project: Project, task: Task, look: string): void {
    const looks = this.looks.get(project.id) ?? new Set<Promise<void>>();
    this.looks.set(project.id, looks);
    const going = (async () => {
      // The task's one Reviewer, already at work on it, reads the request in that turn and answers in it.
      if (this.reviewing.has(task) && await this.callAgent.steer?.(`${project.id}/${task.id}.reviewer`, letterPrompt('engineer', look))) return;
      const handoff = (await this.reviewTurn(task, () => this.call(project, 'reviewer', task, resumed => lookPrompt(project, task, look, resumed), { look: true })))?.handoff;
      if (!handoff) return this.lookFailed(project, task);
      for (const { task: id, letter } of handoff.tell ?? []) if (id !== task.id) await this.taskLetter(project, 'reviewer', id, letter);
      if (task.status === 'open') await this.taskLetter(project, 'reviewer', task.id, read(handoff));
    })().catch(() => this.lookFailed(project, task)).finally(() => {
      looks.delete(going);
      this.works.get(project.id)?.wake();
    });
    looks.add(going);
  }

  /** The session's letterbox, where its role writes as its turn goes on: none outside a server. */
  private box(project: Project, role: Role, task: Task | null, key: string): { letters?: string } {
    const letters = letterbox(key, (to, cc, text) => this.post(project, role, task, to, cc, text));
    return letters ? { letters } : {};
  }

  /** A letter written during a turn, which goes on: each reader named, or copied, has it as they work.
   * Readers: the Manager, the Planner, an open task (its holder), and on the writer's own task the
   * Reviewer, whom the Engineer asks to look now, or the Engineer, whom a Reviewer looking writes to. */
  private async post(project: Project, role: Role, task: Task | null, to: string[], cc: string[], text: string): Promise<string[]> {
    const names = [...new Set([...to, ...cc])];
    if (!names.length) throw new Error('name a reader: to=manager, planner, reviewer, engineer or a task id such as t3 (comma-separated); cc= the same');
    if (!text.trim()) throw new Error('the letter is empty: write it as the request body');
    if (project.status !== 'working') throw new Error(`the project's work is ${project.status}`);
    const deliver = names.map(name => this.reader(project, role, task, name, !to.includes(name)));
    const letter = posted(role, task, to, cc, text);
    for (const reach of deliver) await reach(letter);
    this.record(project, { type: 'letter', role, ...task ? { task: task.id } : {}, to, cc, text });
    return names;
  }

  /** How a letter written during a turn reaches `name`; throws when no such reader can have it. */
  private reader(project: Project, role: Role, task: Task | null, name: string, copy = false): (letter: string) => Promise<void> {
    if (name === 'manager') return async letter => { void this.writeToManager(project, letter).catch(error => this.fail(project, 'manager', null, String(error))); };
    // A copy asks nothing of the Planner: it reads it beside its next letter, and is not woken for it.
    if (name === 'planner' && copy) return async letter => { (project.beside ??= []).push(letter); this.store.save(project); };
    if (name === 'planner') return async letter => { project.inbox.push(letter); this.store.save(project); this.works.get(project.id)?.wake(); };
    if (name === 'reviewer') {
      if (role !== 'engineer' || !task) throw new Error('only an Engineer writes to the Reviewer of their task; for another task, name the task');
      return async letter => this.lookAt(project, task, letter);
    }
    if (name === 'engineer') {
      if (role !== 'reviewer' || !task || task.holder !== 'engineer') throw new Error('the Engineer of your task is not working now: your hand-off reaches them; for another task, name the task');
      return letter => this.taskLetter(project, 'reviewer', task.id, letter);
    }
    if (/^t\d+$/.test(name)) {
      if (name === task?.id) throw new Error(`${name} is your own task: name its reviewer or engineer`);
      if (!openTasks(project).some(open => open.id === name)) throw new Error(`task ${name} is not open`);
      return letter => this.taskLetter(project, role, name, letter);
    }
    throw new Error(`no reader ${JSON.stringify(name)}: name manager, planner, reviewer, engineer or a task id such as t3`);
  }

  /** Each task's Reviewer turns, its looks and reviews alike, one after another in its one session. */
  private readonly reviewing = new Map<Task, Promise<unknown>>();

  private reviewTurn<T>(task: Task, turn: () => Promise<T>): Promise<T> {
    // With none under way the turn starts now, else once the one before it has ended.
    const before = this.reviewing.get(task);
    const next = before ? before.catch(() => {}).then(turn) : turn();
    const settled: Promise<unknown> = next.catch(() => {}).finally(() => { if (this.reviewing.get(task) === settled) this.reviewing.delete(task); });
    this.reviewing.set(task, settled);
    return next;
  }

  /** A look that failed holds nothing up: the Engineer hears of it, and goes on. */
  private async lookFailed(project: Project, task: Task): Promise<void> {
    if (task.status === 'open') await this.taskLetter(project, 'reviewer', task.id, 'The look you asked for failed before the Reviewer wrote back. Go on; ask again if you still need it.');
  }

  /** A letter to the Manager stays in the record until a Manager turn takes it. */
  private writeToManager(project: Project, letter: ManagerLetter): Promise<HandOff | null> {
    if (typeof letter === 'string') letter = { text: letter, input_id: randomUUID() };
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
    // A team letter that comes while the Manager's turn goes on is read in that turn, which answers it.
    const running = this.managerTurns.get(project.id);
    if (running && typeof letter !== 'string' && letter.input_id && !this.readBy.has(letter)
      && await this.callAgent.steer?.(`${project.id}/manager`, steeredPrompt(letter.text))) this.readBy.set(letter, running);
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
    const seen = project.beside?.length ?? 0;
    // The ids the Planner was told its tasks take, though the Manager may open tasks meanwhile.
    const base = project.tasks.length;
    const handoff = (await this.call(project, 'planner', null, () => plannerPrompt(project), { planner_inputs: taken }))?.handoff;
    if (!handoff) return;
    project.inbox.splice(0, taken);
    project.beside?.splice(0, seen);
    if (!project.beside?.length) delete project.beside;
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
  private async taskLetter(project: Project, from: Note['from'], id: string, letter: string): Promise<void> {
    const task = openTasks(project).find(task => task.id === id);
    if (!task) return this.returnLetter(project, from, id, letter);
    const note = { from, text: letter };
    (task.unread ??= []).push(note);
    this.store.save(project);
    if (await this.callAgent.steer?.(`${project.id}/${task.id}.${task.holder}`, letterPrompt(from, letter))) this.steered.add(note);
  }

  /** An unread letter goes back to its writer; a Manager's waits until its current turn ends. */
  private returnLetter(project: Project, from: Note['from'], task: string, letter: string): void {
    const note = unread(task, letter);
    if (from === 'manager') void this.writeToManager(project, note).catch(error => this.fail(project, 'manager', null, String(error)));
    else project.inbox.push(note);
  }

  /** One backend turn that ends in an accepted form; null when it failed (recorded, the work
   * stops) or was interrupted. A refused form goes back once, in the same session, with why. */
  private async call(project: Project, role: Role, task: Task | null, prompt: (resumed: boolean) => string,
                     turn: { refused?: string; answers?: string; input_id?: string; planner_inputs?: number; look?: true; retried?: true; afresh?: string } = {}): Promise<AgentResult | null> {
    const { refused, answers, input_id } = turn;
    const answering = { ...answers ? { answers } : {}, ...input_id ? { input_id } : {} };
    const { signal } = this.options;
    if (signal?.aborted) return null;
    // A task's Reviewer is one session, its looks and reviews alike, kept like the Engineer's.
    const key = task ? `${task.id}.${role}` : role;
    const resume = project.sessions[key];
    let stepped = false;
    const where = task ? { task: task.id } : {};
    const dir = this.store.dir(project.id);
    const own = task && typeof task.place === 'object' ? task.place : null;
    this.record(project, { type: 'call.start', role, ...where, ...answering, ...turn.planner_inputs !== undefined ? { planner_inputs: turn.planner_inputs } : {} });
    // A pack's tools are ready before any role works: the first turn waits for its setup.
    const packs = await prepare(this.store.home);
    const ready = packs.filter(pack => pack.state === 'ready').map(pack => pack.home);
    const standing = instructions(role, project, this.store);
    // Only what the session has not been told: a resumed one learns of changed instructions in its prompt.
    // The digest leaves out the date and the machine's load, which change by themselves.
    const digest = createHash('sha256').update(standing.replace(/^Today is .*$|^\d+ logical CPUs.*$/gm, '')).digest('hex').slice(0, 16);
    const told = resume !== undefined && project.instructed?.[key] !== digest;
    (project.instructed ??= {})[key] = digest;
    let result: AgentResult;
    try {
      result = await this.callAgent({
        backend: project.backend, role, ...TURN[role], cwd: own ? own.dir : project.workdir, instructions: standing,
        prompt: turn.afresh !== undefined ? startedAfresh(turn.afresh, join(dir, 'events.jsonl'), prompt(false)) : refused ? refusedPrompt(refused) : told ? reinstructed(standing, prompt(true)) : prompt(resume !== undefined),
        // Every role reads Argus's record of the project (the chief's, of every project) and the skills, and adds to the team's skills, wiki and tools.
        readDirs: [project.chief ? join(this.store.home, 'projects') : dir, SKILLS, packsHome(this.store.home), ...TURN[role].access === 'write' ? [] : ready], writeDirs: [skillsHome(this.store.home), wikiHome(this.store.home), toolsHome(this.store.home), ...own ? [project.workdir] : [], ...TURN[role].access === 'write' ? ready : []],
        env: { ...withTools(packEnv(packs, project.workdir, process.env), toolsHome(this.store.home)), ...author(role, task) }, ...(modelOf(project, role) ? { model: modelOf(project, role)! } : {}),
        ...(effortOf(project, role) ? { effort: effortOf(project, role)! } : {}), ...(resume ? { resume } : {}), ...(signal ? { signal } : {}), session: `${project.id}/${key}`,
        ...(task ? { giver: task.from } : {}), ...this.box(project, role, task, `${project.id}/${key}`), onStep: step => { stepped = true; this.record(project, { type: 'step', role, ...where, ...step }); },
        onWords: (words, kind) => this.options.onWords?.(role, task?.id, words, kind), callsDir: join(dir, 'calls'), usageFile: join(dir, 'usage.jsonl'),
      });
    } catch (thrown) {
      // The call never ran to an end of its own: it is recorded as ended, failed.
      const error = thrown instanceof Error ? thrown.message : String(thrown);
      this.record(project, { type: 'call.end', role, ...where, callId: '', threadId: null, completed: false, error, model: null, usage: null, ms: 0 });
      const again = await this.tryAgain(project, role, key, turn, error);
      if (again) return this.call(project, role, task, prompt, again);
      if (!signal?.aborted && !turn.look) this.fail(project, role, task, error);
      return null;
    }
    const { callId, threadId, completed, error, model, usage, ms, handoff, unpaid } = result;
    // What the turn changed of the team's library is kept, under its name, in the library's history.
    if (TURN[role].access === 'write') {
      const said = (handoff?.letters[0] ?? '').split('\n')[0]!.slice(0, 200);
      keepLibrary(this.store.home, `${role}${task ? ` on ${task.id}` : ''} (${project.id})${said ? `: ${said}` : ''}`);
    }
    // A turn goes on next time in the session its backend reported, whether it ended, was cut off
    // or failed: its work is not done again. With none reported, or a new one whose turn failed before
    // a step, which holds no work and may hold the settings it failed on, the role starts afresh; and
    // a session reset while the turn went on stays reset.
    if (project.sessions[key] === resume) {
      if (threadId && (resume || completed || stepped || signal?.aborted)) project.sessions[key] = threadId;
      else delete project.sessions[key];
    }
    // The project's model, left to the backend, is what a call without a model of its own reported.
    if (!project.model && model && !project.models?.[role]) {
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
      // An empty letter to oneself is a turn that had nothing to say: it is not recorded.
      for (const [at, letter] of letters.entries()) this.record(project, { type: 'handoff', role, ...where,
        ...(tasks?.[at]?.task ? { task: tasks[at]!.task } : {}), to, letter, ...delivered, ...answering });
      return result;
    }
    if (completed && threadId && !refused) return this.call(project, role, task, prompt, { ...turn, refused: error ?? '' });
    const again = !completed && !unpaid ? await this.tryAgain(project, role, key, turn, error ?? 'The turn failed.') : null;
    if (again) return this.call(project, role, task, prompt, again);
    if (!signal?.aborted && !turn.look) this.fail(project, role, task, completed ? `The ${role}'s hand-off was refused: ${error}` : error ?? `The ${role}'s turn failed.`, unpaid);
    return null;
  }

  /** A failed turn holds up its role, on its task if it was on one, and the rest of the work goes
   * on; the project shows it failed once nothing else does, unless the operator stopped it. */
  /** How a call that failed goes on by itself, if it does: first in its session, as most failures pass;
   * then in a new one, for a session that itself cannot go on. A new one has lost the session's memory, not
   * its work. The Manager's failure shows at once to the operator who is talking to it. */
  private async tryAgain<Turn extends { look?: true; retried?: true; afresh?: string; refused?: string }>(project: Project, role: Role, key: string, turn: Turn, error: string): Promise<Turn | null> {
    const { retryFailedAfterMs: wait, signal } = this.options;
    if (wait === undefined || role === 'manager' || turn.look || signal?.aborted) return null;
    if (turn.afresh !== undefined || (turn.retried && !project.sessions[key])) return null;
    await pause(wait, signal);
    if (signal?.aborted || project.status === 'stopped') return null;
    if (!turn.retried) return { ...turn, retried: true };
    delete project.sessions[key];
    this.store.save(project);
    const { refused: _, ...rest } = turn;
    return { ...rest, afresh: error } as Turn;
  }

  private fail(project: Project, role: Role, task: Task | null, error: string, unpaid?: true): void {
    Object.assign(project, { failures: [...project.failures ?? [], { role, task: task?.id ?? null, error, ...unpaid && { unpaid } }], error });
    const goesOn = this.works.has(project.id);
    if (goesOn || project.status === 'stopped') this.store.save(project);
    else this.status(project, 'failed', error);
    // Beside work that goes on, nothing else shows that this part of it waits: the operator is told.
    if (this.options.retryFailedAfterMs !== undefined && goesOn && role !== 'manager' && project.status !== 'stopped') {
      void this.writeToManager(project, heldLetter(role, task, error)).catch((thrown: unknown) => this.fail(project, 'manager', null, String(thrown)));
    }
  }

  /** A letter to the Engineer opens a task; its first sentence names it. It waits only for tasks opened
   * before it, so no tasks wait for each other in a circle. */
  private open(project: Project, from: Task['from'], letter: string, after: string[] = []): Task {
    const named = title(letter);
    const waits = [...new Set(after)].filter(id => project.tasks.some(task => task.id === id));
    const task: Task = {
      id: `t${project.tasks.length + 1}`, title: named, from, objective: letter, status: 'open', holder: 'engineer', note: null, ...waits.length ? { after: waits } : {},
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
    if (typeof task.place === 'object') {
      try {
        const deleted = forgetWorktree(project.workdir, task.place.dir);
        this.record(project, { type: 'task.worktree', task: task.id, dir: task.place.dir, ...deleted ? { removed: true } : { kept: true } });
      } catch (error) { this.record(project, { type: 'task.worktree', task: task.id, dir: task.place.dir, error: String(error) }); }
    }
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
    // A projection that cannot be written holds nothing up.
    if (event.type !== 'step') try { this.progress.note(project, event); } catch { /* rebuilt at the next event */ }
    this.options.onEvent?.(event);
  }
}

export const openTasks = ({ tasks }: Project): Task[] => tasks.filter(task => task.status === 'open');

/** A letter's first line as a heading reads it: a link as its words, without markup. It names the
 * task the letter opens. */
export const firstLine = (text: string): string => text.split('\n')
  .map(line => line.replace(/^#+\s*/, '').replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/\*\*|`/g, '').trim()).find(Boolean) ?? '';

/** What names a project or task: the first sentence of a letter's first line, as a letter that opens
 * with a whole paragraph would otherwise put that paragraph in the heading. */
export const title = (text: string): string => {
  const line = firstLine(text);
  return line.match(/^.+?(?:[。！？](?=.)|[.!?](?=\s))/u)?.[0] ?? line;
};

/** A hand-off's letters, read as one: there is more than one only when the Planner opens tasks. */
const read = ({ letters }: HandOff): string => letters.join('\n\n');

/** Whether a question or a failed turn holds up the task, or with none, the Planner: it waits for the operator. */
const held = ({ questions, failures = [] }: Project, task: Task | null): boolean =>
  [...questions, ...failures].some(hold => task ? hold.task === task.id : hold.role === 'planner');

/** Whether the task's next turn can start: nothing holds it up, and the tasks it waits for have closed. */
const ready = (project: Project, task: Task): boolean => !held(project, task)
  && (task.after ?? []).every(id => project.tasks.find(other => other.id === id)?.status === 'closed');

/** Who made a commit, as git records it: the role, on its task. The history then says who did what. */
export function author(role: Role, task: Task | null): Record<string, string> {
  return { GIT_AUTHOR_NAME: `${NAMES[role]}${task ? ` ${task.id}` : ''}`, GIT_AUTHOR_EMAIL: `${role}${task ? `.${task.id}` : ''}@argus.local` };
}

/** A call's environment with the team's tools first on its PATH. */
export function withTools(env: Record<string, string>, dir: string): Record<string, string> {
  const path = env.PATH ?? process.env.PATH ?? '';
  return { ...env, PATH: path ? `${dir}${delimiter}${path}` : dir };
}
