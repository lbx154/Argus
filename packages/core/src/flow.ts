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
import { readFileSync } from 'node:fs';
import type { UploadedAttachment } from '@argus/contracts';
import type { Access, AgentResult, CallAgent, Form, HandOff, Role } from './types.js';
import { effortOf, modelOf, type FlowEvent, type Note, type OperatorMessage, type ManagerLetter, type Project, type Question, type Status, type Store, type Task } from './store.js';
import { answered, closedTo, copied, freshSession, overflow, NAMES, heldLetter, startedAfresh, steeredPrompt, handedTo, posted, reinstructed, instructions, lookPrompt, letterPrompt, unread, managerPrompt, packsHome, plannerPrompt, refusedPrompt, SKILLS, skillsHome, toolsHome, wikiHome, taskPrompt } from './prompts.js';
import { keep, titleOf, under } from './serve/files.js';
import { nameLegacyInputs } from './inputs.js';
import { forgetWorktree, freeze, isCheckout, makeWorktree } from './worktrees.js';
import { Progress } from './progress.js';
import { PROGRESS_BRANCH, publish } from './publish.js';
import { closeLetterbox, letterbox } from './letters.js';
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
  /** A backend that cannot take a letter into a turn under way (Copilot's runs a turn to its end) reads
   * one from the Planner or Manager this long after it came: Argus ends the turn, and it goes on in its
   * session with its letters. Unset, letters wait for the turn's end. */
  deliverLettersAfterMs?: number;
  /** A project with a publish remote is pushed there this long after a task closes or its status changes
   * (one push for what comes together), and every `publishEveryMs` while it works. Unset, never. */
  publishAfterMs?: number;
  publishEveryMs?: number;
  /** The git credential helper Argus pushes with, in place of the host's own. */
  gitCredentialHelper?: string;
}

/** Waits, or stops waiting when the work is interrupted. */
/** How many times a turn that ended without its form goes back before it counts as failed. */
const NUDGES = 3;

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
  /** When each task was opened and when it last did anything, as this process saw it: the pace the Manager reads. */
  private readonly opened = new Map<string, number>();
  private readonly active = new Map<string, number>();

  /** Where the work's time goes, for the Manager at each closed task: what runs, what waits, what has gone quiet. */
  private pace(project: Project): string {
    const now = Date.now();
    const ago = (ms: number): string => ms < 60_000 ? 'under a minute' : ms < 3_600_000 ? `${Math.round(ms / 60_000)} min` : `${(ms / 3_600_000).toFixed(1)} h`;
    const open = openTasks(project);
    if (!open.length) return '';
    const running = open.filter(task => this.running.has(`${project.id}/${task.id}.${task.holder}`));
    const waiting = open.filter(task => (task.after ?? []).some(id => project.tasks.some(other => other.id === id && other.status === 'open')));
    const rows = open.map(task => {
      const key = `${project.id}/${task.id}`;
      const since = this.opened.get(key);
      const last = this.active.get(key);
      const state = running.includes(task) ? `the ${NAMES[task.holder]} at work` : waiting.includes(task) ? `waits for ${(task.after ?? []).join(', ')}` : 'no turn under way';
      return `- ${task.id} (${task.title}): ${state}; open ${since ? ago(now - since) : 'since before Argus last started'}; last step ${last ? `${ago(now - last)} ago` : 'not seen since Argus last started'}`;
    });
    const quiet = open.filter(task => { const last = this.active.get(`${project.id}/${task.id}`); return last !== undefined && now - last > 30 * 60_000; });
    const long = open.filter(task => { const since = this.opened.get(`${project.id}/${task.id}`); return since !== undefined && now - since > 3 * 3_600_000; });
    return [
      `${running.length} of ${open.length} open tasks have a turn under way; ${waiting.length} wait for others.`,
      ...rows,
      ...quiet.length ? [`Quiet for over 30 minutes: ${quiet.map(task => task.id).join(', ')}.`] : [],
      ...long.length ? [`Open for over 3 hours: ${long.map(task => task.id).join(', ')}: one that has grown past one result is worth splitting.`] : [],
    ].join('\n');
  }

  /** Each project's next push, while one waits; and the projects being pushed now. */
  private readonly publishing = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly pushing = new Set<string>();

  /** The project goes to its publish remote soon: a push already waiting takes what comes before it. */
  private publishSoon(project: Project, after = this.options.publishAfterMs): void {
    if (!project.publish || after === undefined || this.publishing.has(project.id)) return;
    const timer = setTimeout(() => {
      this.publishing.delete(project.id);
      void this.publishNow(project);
    }, after);
    timer.unref?.();
    this.publishing.set(project.id, timer);
  }

  /** Pushes the workspace's branches and Argus's page; what the remote refused, or why nothing went, is recorded. */
  private async publishNow(project: Project): Promise<void> {
    const remote = project.publish;
    if (!remote) return;
    if (this.pushing.has(project.id)) return this.publishSoon(project);
    this.pushing.add(project.id);
    try {
      const dir = this.store.dir(project.id);
      let page = '';
      try { page = readFileSync(join(dir, 'PROGRESS.md'), 'utf8'); } catch { /* none written yet */ }
      const readme = `# Where the work stands\n\nArgus keeps this branch (${PROGRESS_BRANCH}) for project ${project.id}: PROGRESS.md is its page of the work, `
        + `pushed when a task closes, when the work rests, and every while as it goes on. The team's own branches are beside it.\n`;
      const result = await publish(project.workdir, remote, { 'PROGRESS.md': page, 'README.md': readme }, this.options.gitCredentialHelper);
      if (result.pushed.length || result.rejected.length || result.error) this.record(project, { type: 'published', remote, ...result });
    } catch (error) {
      this.record(project, { type: 'published', remote, pushed: [], rejected: [], error: String(error) });
    } finally {
      this.pushing.delete(project.id);
    }
    // While the work goes on, it is pushed again every while, whether or not a task closes.
    if (project.status === 'working' && this.options.publishEveryMs !== undefined) this.publishSoon(project, this.options.publishEveryMs);
  }

  /** The operator's message. The Manager reads it in its next turn, and what its letter hands on
   * joins the work; like a letter from the team it waits in the record until a turn takes it.
   * Resolves with the Manager's hand-off, or null when the turn failed or was interrupted. */
  say(id: string, text: string, { request_id = randomUUID(), attachments }: Omit<OperatorMessage, 'text'> = {}): Promise<HandOff | null> {
    const project = this.project(id);
    // The message is named: the Manager's turn on it, and the letter that answers it, name it in turn.
    const message: OperatorMessage = { text, request_id, ...attachments?.length ? { attachments } : {} };
    // A message to a stopped project takes the place of the operator's the stop left unanswered, not
    // of one sent since that a Manager turn is taking; the team's letters wait for Resume.
    const taking = (this.handing.get(project) ?? []).map(entry => entry.letter);
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
    const under = (this.handing.get(project) ?? []).map(entry => entry.letter);
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

  /** Pushes the project to its publish remote now, as it stands. */
  publish(id: string): void { this.publishSoon(this.project(id), 0); }

  /** The project once nothing is under way on it: no Manager turn, no work. */
  async settled(id: string): Promise<Project> {
    for (let busy = this.busy(id); busy.length; busy = this.busy(id)) await Promise.all(busy);
    return this.project(id);
  }

  private busy(id: string): Promise<unknown>[] {
    const project = this.projects.get(id);
    const handing = project ? (this.handing.get(project) ?? []).map(entry => entry.done) : [];
    return [...handing, this.managing.get(id), this.works.get(id)?.done].filter(promise => promise !== undefined);
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
      const handing = this.going(project) ? (this.handing.get(project) ?? []).map(entry => entry.done) : [];
      if (!running.size && !looks.size && !handing.length) break;
      await Promise.race([...running.values(), ...looks, ...handing, new Promise<void>(resolve => { work.wake = resolve; })]);
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

  /** As a task begins it works in the workspace, unless another open task already works on the project;
   * then, in a git checkout, in a worktree of its own. Elsewhere tasks share the workspace, as before. */
  private place(project: Project, task: Task): void {
    if (task.place) return;
    const shared = project.tasks.some(other => other !== task && other.status === 'open' && Boolean(other.place));
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
          this.inWorkspaceOnceDeleted(project, task);
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
        // The Manager keeps the whole in view: every task that closes reaches it too, accepted or not.
        this.tellManager(project, closedTo(role, task, letter, this.pace(project)));
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
    if (task && task.status !== 'open') throw new Error(`task ${task.id} is not open`);
    const deliver = names.map(name => this.reader(project, role, task, name, !to.includes(name)));
    const letter = posted(role, task, to, cc, text);
    for (const reach of deliver) await reach(letter);
    this.record(project, { type: 'letter', role, ...task ? { task: task.id } : {}, to, cc, text });
    return names;
  }

  /** How a letter written during a turn reaches `name`; throws when no such reader can have it. */
  private reader(project: Project, role: Role, task: Task | null, name: string, copy = false): (letter: string) => Promise<void> {
    if (name === 'manager') return async letter => { this.tellManager(project, letter); };
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

  /** Sends a team or host letter to the Manager alongside work under way. */
  private tellManager(project: Project, letter: ManagerLetter): void {
    try {
      void this.writeToManager(project, letter).catch(error => {
        try { if (!this.options.signal?.aborted) this.fail(project, 'manager', null, String(error)); } catch { /* store gone */ }
      });
    } catch { /* store gone */ }
  }

  /** The letters put into a turn under way, which its hand-off shows it has read. */
  private readonly steered = new WeakSet<Note>();
  /** The sessions whose turn is under way, by key (project/task.role). */
  private readonly running = new Set<string>();
  /** Sessions whose turn Argus ended to give them their letters: they go on at once. */
  private readonly delivering = new Set<string>();

  /** Each project's letters a Manager turn is taking or will take, one entry for each. */
  private readonly handing = new Map<Project, Array<{ letter: ManagerLetter; done: Promise<unknown> }>>();

  private async handOver(project: Project, letter: ManagerLetter): Promise<HandOff | null> {
    let finish = (): void => {};
    const entry = { letter, done: new Promise<void>(resolve => { finish = resolve; }) };
    const list = this.handing.get(project) ?? [];
    list.push(entry);
    this.handing.set(project, list);
    try {
      // A team letter that comes while the Manager's turn goes on is read in that turn, which answers it.
      const running = this.managerTurns.get(project.id);
      if (running && typeof letter !== 'string' && letter.input_id && !this.readBy.has(letter)
        && await this.callAgent.steer?.(`${project.id}/manager`, steeredPrompt(letter.text))) this.readBy.set(letter, running);
      const handoff = await this.manager(project, letter);
      const at = project.forManager?.indexOf(letter) ?? -1;
      if (handoff && at >= 0) {
        project.forManager!.splice(at, 1);
        this.store.save(project);
      }
      return handoff;
    } finally {
      const now = this.handing.get(project);
      const index = now ? now.indexOf(entry) : -1;
      if (index >= 0) now!.splice(index, 1);
      finish();
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
      const start = project.tasks.length;
      for (const [at] of letters.entries()) {
        if (!tasks?.[at]?.task) opened.set(`t${base + opened.size + 1}`, `t${start + opened.size + 1}`);
      }
      const existing = new Set(project.tasks.map(task => task.id));
      const added = new Set(opened.values());
      const accepted = new Map<string, string[]>();
      const reaches = (from: string, target: string, visited = new Set<string>()): boolean =>
        from === target || !visited.has(from) && Boolean(visited.add(from)) && (accepted.get(from) ?? []).some(next => reaches(next, target, visited));
      const steers: Promise<void>[] = [];
      let created = 0;
      for (const [at, letter] of letters.entries()) {
        const { task: id = '', after = [], withdraw } = tasks?.[at] ?? {};
        if (!id) {
          const own = `t${start + ++created}`;
          const waits: string[] = [];
          for (const raw of after) {
            const dep = opened.get(raw) ?? raw;
            if (dep === own || waits.includes(dep)) continue;
            if (existing.has(dep)) waits.push(dep);
            else if (added.has(dep) && !reaches(dep, own)) {
              waits.push(dep);
              accepted.set(own, waits);
            }
          }
          this.open(project, 'planner', letter, waits, added);
        }
        else if (withdraw) this.withdraw(project, id, letter);
        else steers.push(this.taskLetter(project, 'planner', id, letter));
      }
      await Promise.all(steers);
      this.store.save(project);
    }
    else if (to === 'operator') this.ask(project, 'planner', null, read(handoff));
    else if (to === 'manager') await this.writeToManager(project, handedTo('planner', null, read(handoff)));
  }

  /** The Planner ends an open task, its letter the task's last word: the turn under way stops (close ends its
   * sessions), and what it did stays where it is. On virtiofs a task told its work was done elsewhere went on
   * redoing it for hours, a letter being all the Planner had. */
  private withdraw(project: Project, id: string, letter: string): void {
    const task = openTasks(project).find(task => task.id === id);
    if (!task) return this.returnLetter(project, 'planner', id, letter);
    this.close(project, task, 'planner', letter);
  }

  /** A letter stays on its task till a turn that read it ends; a backend that takes one reads it as it goes. */
  private async taskLetter(project: Project, from: Note['from'], id: string, letter: string): Promise<void> {
    const task = openTasks(project).find(task => task.id === id);
    if (!task) return this.returnLetter(project, from, id, letter);
    const note = { from, text: letter };
    (task.unread ??= []).push(note);
    this.store.save(project);
    const session = `${project.id}/${task.id}.${task.holder}`;
    if (await this.callAgent.steer?.(session, letterPrompt(from, letter))) this.steered.add(note);
    else if ((from === 'planner' || from === 'manager') && this.running.has(session)) this.deliverLater(project, task, note, session);
  }

  /** Whether Argus ended this session's last turn to give it its letters; it hears so once. */
  private delivered(session: string): boolean {
    const was = this.delivering.has(session) && !this.running.has(session);
    if (was) this.delivering.delete(session);
    return was;
  }

  /** A letter a turn under way could not take: if it is still unread after a while, the turn ends,
   * and the task goes on in its session with what it was sent. */
  private deliverLater(project: Project, task: Task, note: Note, session: string): void {
    const { deliverLettersAfterMs: wait, signal } = this.options;
    if (wait === undefined || this.delivering.has(session)) return;
    this.delivering.add(session);
    void pause(wait, signal).then(async () => {
      if (signal?.aborted || !this.delivering.has(session) || !this.running.has(session) || !task.unread?.includes(note) || !await this.callAgent.interrupt?.(session)) {
        this.delivering.delete(session);
        return;
      }
      this.record(project, { type: 'letters.delivered', task: task.id, role: task.holder, letters: task.unread.length });
    });
  }

  /** An unread letter goes back to its writer; a Manager's waits until its current turn ends. */
  private returnLetter(project: Project, from: Note['from'], task: string, letter: string): void {
    const note = unread(task, letter);
    if (from === 'manager') this.tellManager(project, note);
    else project.inbox.push(note);
  }

  /** One backend turn that ends in an accepted form; null when it failed (recorded, the work
   * stops) or was interrupted. A refused form goes back once, in the same session, with why. */
  private async call(project: Project, role: Role, task: Task | null, prompt: (resumed: boolean) => string,
                     turn: { refused?: string; answers?: string; input_id?: string; planner_inputs?: number; look?: true; retried?: true; afresh?: string; nudged?: number } = {}): Promise<AgentResult | null> {
    const { refused, answers, input_id } = turn;
    const answering = { ...answers ? { answers } : {}, ...input_id ? { input_id } : {} };
    const { signal } = this.options;
    if (signal?.aborted) return null;
    // A task's Reviewer is one session, its looks and reviews alike, kept like the Engineer's.
    const key = task ? `${task.id}.${role}` : role;
    // A session past the project's limit of context goes on in a fresh one, which searches the old one's record.
    const ran = project.contexts?.[key];
    if (task) this.inWorkspaceOnceDeleted(project, task);
    // A session lives in its folder: once the task's worktree is gone, it cannot be resumed in the workspace.
    const moved = project.moved?.includes(key) ?? false;
    if (moved) project.moved = project.moved!.filter(other => other !== key);
    const fresh = project.sessions[key] !== undefined && (moved || (project.freshAt !== undefined && ran !== undefined && ran > project.freshAt))
      ? this.recordOf(project, project.sessions[key]!) : null;
    if (fresh) {
      this.record(project, { type: 'session.fresh', role, ...task ? { task: task.id } : {}, context: moved ? 'moved' : ran!, calls: fresh });
      delete project.sessions[key];
      if (project.contexts) delete project.contexts[key];
    }
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
    // The digest leaves out the date and the machine's load, which change by themselves, and the
    // length of each wiki page, which changes whenever anyone edits one; a new page still counts.
    const digest = createHash('sha256').update(standing.replace(/^Today is .*$|^\d+ logical CPUs.*$/gm, '').replace(/ \(\d+ lines\)/g, '')).digest('hex').slice(0, 16);
    const told = resume !== undefined && project.instructed?.[key] !== digest;
    (project.instructed ??= {})[key] = digest;
    let result: AgentResult;
    const session = `${project.id}/${key}`;
    this.running.add(session);
    try {
      const protectDirs = openTasks(project)
        .flatMap(other => other !== task && typeof other.place === 'object' ? [other.place.dir] : []);
      result = await this.callAgent({
        backend: project.backend, role, ...TURN[role], cwd: own ? own.dir : project.workdir, instructions: standing,
        prompt: turn.afresh !== undefined ? startedAfresh(turn.afresh, join(dir, 'events.jsonl'), prompt(false)) : fresh ? freshSession(moved ? 'moved' : ran!, fresh, prompt(false)) : refused ? refusedPrompt(refused) : told ? reinstructed(standing, prompt(true)) : prompt(resume !== undefined),
        // Every role reads Argus's record of the project (the chief's, of every project) and the skills, and adds to the team's skills, wiki and tools.
        readDirs: [project.chief ? join(this.store.home, 'projects') : dir, SKILLS, packsHome(this.store.home), ...TURN[role].access === 'write' ? [] : ready], writeDirs: [skillsHome(this.store.home), wikiHome(this.store.home), toolsHome(this.store.home), ...own ? [project.workdir] : [], ...TURN[role].access === 'write' ? ready : []],
        ...protectDirs.length ? { protectDirs } : {},
        env: { ...withTools(packEnv(packs, project.workdir, process.env), toolsHome(this.store.home)), ...author(role, task) }, ...(modelOf(project, role) ? { model: modelOf(project, role)! } : {}),
        ...(effortOf(project, role) ? { effort: effortOf(project, role)! } : {}),
        ...(project.freshAt ? { freshAt: project.freshAt, ...role === 'engineer' ? { overflow: overflow(project.freshAt) } : {} } : {}), ...(resume ? { resume } : {}), ...(signal ? { signal } : {}), session: `${project.id}/${key}`,
        ...(task ? { giver: task.from } : {}), ...this.box(project, role, task, `${project.id}/${key}`), onStep: step => { stepped = true; this.record(project, { type: 'step', role, ...where, ...step }); },
        onWords: (words, kind) => this.options.onWords?.(role, task?.id, words, kind), callsDir: join(dir, 'calls'), usageFile: join(dir, 'usage.jsonl'),
      });
    } catch (thrown) {
      this.running.delete(session);
      // The call never ran to an end of its own: it is recorded as ended, failed.
      const error = thrown instanceof Error ? thrown.message : String(thrown);
      this.record(project, { type: 'call.end', role, ...where, callId: '', threadId: null, completed: false, error, model: null, usage: null, ms: 0 });
      // A task withdrawn while its turn went on is over: nothing of the turn is taken or tried again.
      if (task && task.status !== 'open') return null;
      if (this.delivered(session)) return this.call(project, role, task, prompt, turn);
      const again = await this.tryAgain(project, role, key, turn, error);
      if (again) return this.call(project, role, task, prompt, again);
      if (!signal?.aborted && !turn.look) this.fail(project, role, task, error);
      return null;
    }
    this.running.delete(session);
    const { callId, threadId, completed, error, model, usage, ms, handoff, unpaid, context } = result;
    // A turn that ended by itself: the next one reads every letter waiting, so none is delivered by ending it.
    if (completed) this.delivering.delete(session);
    if (context !== undefined) (project.contexts ??= {})[key] = context;
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
    if (task && task.status !== 'open') return null;
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
    // A turn Argus ended to give it its letters goes on at once, in its session, with them.
    if (!completed && !signal?.aborted && this.delivered(session)) return this.call(project, role, task, prompt, turn);
    // A turn that ended without its form goes back, in its session, saying why: some models end a turn on
    // a sentence about what they will do next. After a few, it is a failed turn.
    const nudged = turn.nudged ?? 0;
    if (completed && threadId && nudged < NUDGES) return this.call(project, role, task, prompt, { ...turn, refused: error ?? '', nudged: nudged + 1 });
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
  private async tryAgain<Turn extends { look?: true; retried?: true; afresh?: string; refused?: string; nudged?: number }>(project: Project, role: Role, key: string, turn: Turn, error: string): Promise<Turn | null> {
    const { retryFailedAfterMs: wait, signal } = this.options;
    if (wait === undefined || role === 'manager' || turn.look || signal?.aborted) return null;
    if (turn.afresh !== undefined || (turn.retried && !project.sessions[key])) return null;
    await pause(wait, signal);
    if (signal?.aborted || project.status === 'stopped') return null;
    if (!turn.retried) return { ...turn, retried: true };
    delete project.sessions[key];
    this.store.save(project);
    const { refused: _, nudged: __, ...rest } = turn;
    return { ...rest, afresh: error } as Turn;
  }

  private fail(project: Project, role: Role, task: Task | null, error: string, unpaid?: true): void {
    Object.assign(project, { failures: [...project.failures ?? [], { role, task: task?.id ?? null, error, ...unpaid && { unpaid } }], error });
    const goesOn = this.works.has(project.id);
    if (goesOn || project.status === 'stopped') this.store.save(project);
    else this.status(project, 'failed', error);
    // Beside work that goes on, nothing else shows that this part of it waits: the operator is told.
    if (this.options.retryFailedAfterMs !== undefined && goesOn && role !== 'manager' && project.status !== 'stopped') {
      this.tellManager(project, heldLetter(role, task, error));
    }
  }

  /** A letter to the Engineer opens a task; its first sentence names it. Its `after` has no circle. */
  private open(project: Project, from: Task['from'], letter: string, after: string[] = [], known?: ReadonlySet<string>): Task {
    const named = title(letter);
    const waits = [...new Set(after)].filter(id => known?.has(id) || project.tasks.some(task => task.id === id));
    const task: Task = {
      id: `t${project.tasks.length + 1}`, title: named, from, objective: letter, status: 'open', holder: 'engineer', note: null, ...waits.length ? { after: waits } : {},
    };
    project.tasks.push(task);
    this.record(project, { type: 'task.added', task });
    return task;
  }

  /** The raw records of the calls a backend session took, for a fresh session to search. */
  private recordOf(project: Project, thread: string): string[] {
    const calls = join(this.store.dir(project.id), 'calls');
    let text = '';
    try { text = readFileSync(join(this.store.dir(project.id), 'events.jsonl'), 'utf8'); } catch { return []; }
    const ids = new Set<string>();
    for (const line of text.split('\n')) {
      if (!line.includes('"call.end"') || !line.includes(thread)) continue;
      try { const event = JSON.parse(line) as { type: string; threadId?: string; callId?: string }; if (event.threadId === thread && event.callId) ids.add(event.callId); } catch { /* a line cut short */ }
    }
    return [...ids].map(id => join(calls, `${id}.jsonl`));
  }

  /** A task may delete its own worktree once its changes are in the workspace: from then on it works
   * there, and its candidate is frozen there. Argus only notes that the worktree is gone. */
  private inWorkspaceOnceDeleted(project: Project, task: Task): void {
    if (typeof task.place !== 'object' || !forgetWorktree(project.workdir, task.place.dir)) return;
    this.record(project, { type: 'task.worktree', task: task.id, dir: task.place.dir, removed: true });
    task.place = 'workspace';
    // Its sessions stayed in the worktree's folder; each goes on in a fresh one that searches its record.
    const own = Object.keys(project.sessions).filter(key => key.startsWith(`${task.id}.`));
    if (own.length) project.moved = [...new Set([...project.moved ?? [], ...own])];
    this.store.save(project);
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
    closeLetterbox(`${project.id}/${task.id}.`);
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
    if (event.type === 'task.closed' || event.type === 'status') this.publishSoon(project);
    if (event.type === 'task.added') this.opened.set(`${project.id}/${event.task.id}`, Date.now());
    else if ('task' in event && typeof event.task === 'string') this.active.set(`${project.id}/${event.task}`, Date.now());
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
