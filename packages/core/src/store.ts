// The state root: one process owns it (owner.json), and each project is a directory with its
// current state (project.json, replaced atomically) and its history (events.jsonl, appended).
// A fact lives in project.json only; events record changes and are never read back as state.

import { appendFileSync, closeSync, fstatSync, mkdirSync, openSync, readdirSync, readFileSync, readSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import type { DeliverySnapshot, UploadedAttachment } from '@argus/contracts';
import type { Backend, Role, Step, Usage } from './types.js';

/** `idle`: no letter is waiting for anyone but the operator; `asking`: nothing goes on but what
 * waits for the operator's answers; `failed`: nothing goes on but what waits for the operator to
 * resume failed turns; `stopped`: the operator stopped the work, and no turn starts till they
 * resume it or ask the Manager for more. */
export type Status = 'working' | 'asking' | 'stopped' | 'failed' | 'idle';
/** A task opens with a letter to the Engineer and closes with a letter from it, or from its
 * Reviewer, to anyone but the two of them. */
export type TaskStatus = 'open' | 'closed';
export type Holder = 'engineer' | 'reviewer';

export interface Note { from: Role | 'operator'; text: string }

export interface Task {
  id: string;
  title: string;
  /** Who gave it, the Manager or the Planner, and the letter that did: its whole brief. */
  from: 'manager' | 'planner';
  objective: string;
  status: TaskStatus;
  holder: Holder;
  /** The last letter on it: what the holder reads next, or for a closed task, its last word. */
  note: Note | null;
  /** The tasks it waits for: it starts once they have all closed. */
  after?: string[];
  /** Letters to whoever holds it, written while they worked, that their turn could not take: they read them at their next. */
  unread?: { from: Task['from']; text: string }[];
}

/** A question to the operator: its asker, the task's holder or the Planner, waits for the answer. */
export interface Question {
  role: Role;
  task: string | null;
  /** The asker's letter. */
  question: string;
}

/** A turn that failed: its role waits, on its task if it was on one, till the operator resumes the
 * work; the rest of the work goes on. */
export interface Failure {
  role: Role;
  task: string | null;
  error: string;
}

export interface OperatorMessage { text: string; request_id?: string; attachments?: UploadedAttachment[] }

export type ManagerLetter = string | OperatorMessage;

export interface Project {
  id: string;
  created: string;
  workdir: string;
  request: string;
  /** The first operator message's request id; absent in older projects. */
  requestId?: string;
  backend: Backend;
  model: string | null;
  /** The reasoning effort of every call, in the backend's word for it; absent, the backend's default. */
  effort?: string;
  /** The project's name: the operator's, or fixed by the first request's Manager turn;
   * absent, the page names it by its request. */
  name?: string;
  status: Status;
  /** Why the last call failed, while a failed turn waits. */
  error: string | null;
  /** The operator's open questions, one per asker at most; the rest of the work goes on. */
  questions: Question[];
  /** The failed turns that wait for the operator to resume the work. */
  failures?: Failure[];
  tasks: Task[];
  /** Letters to the Planner, which it reads as they come. */
  inbox: string[];
  /** Letters to the Manager, the operator's and the team's, that no Manager turn has taken yet. */
  forManager?: ManagerLetter[];
  /** Backend session per role: manager, planner, <task>.engineer, <task>.reviewer. */
  sessions: Record<string, string>;
  /** The versions kept of each file a result was delivered with, oldest first: copies in delivered/. */
  delivered?: Record<string, string[]>;
}

export type FlowEvent =
  /** The operator's words: to the Manager, or to the role whose question they answer. */
  | ({ type: 'message'; from: 'operator'; to?: Role; task?: string } & OperatorMessage)
  /** A Manager turn on an operator message, and the letter that ends it, name the message they answer. */
  | { type: 'call.start'; role: Role; task?: string; answers?: string }
  | { type: 'call.end'; role: Role; task?: string; callId: string; threadId: string | null; completed: boolean;
      error: string | null; model: string | null; usage: Usage | null; ms: number }
  /** A delivered result's files (`open`), each as it was then (`snapshots`). */
  | { type: 'handoff'; role: Role; task?: string; to: string; letter: string; open?: string[]; snapshots?: DeliverySnapshot[]; title?: string; answers?: string }
  | { type: 'task.added'; task: Task }
  | { type: 'task.closed'; task: string }
  | ({ type: 'step'; role: Role; task?: string } & Step)
  | { type: 'status'; status: Status; error?: string };

/** A project's id, and the name of its directory: `p-` and hex. */
export const PROJECT_ID = /^p-[0-9a-f]+$/;

/** The state root: $ARGUS_HOME, absolute, or ~/.argus. */
export const argusHome = (): string => resolve(process.env.ARGUS_HOME || join(homedir(), '.argus'));

export class Store {
  constructor(readonly home = argusHome()) {}

  dir(id: string): string { return join(this.home, 'projects', id); }

  /** Take the state root for this process; the returned function gives it back. */
  own(): () => void {
    mkdirSync(this.home, { recursive: true });
    const path = join(this.home, 'owner.json');
    for (;;) {
      try {
        writeFileSync(path, JSON.stringify({ pid: process.pid, started: new Date().toISOString() }) + '\n', { flag: 'wx' });
        return () => { if (ownerPid(path) === process.pid) rmSync(path, { force: true }); };
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
      }
      const pid = ownerPid(path);
      if (pid !== null && alive(pid)) throw new Error(`${this.home} is in use by process ${pid}`);
      rmSync(path, { force: true });
    }
  }

  /** A new project; without a workdir it works in its own under $ARGUS_HOME/workspaces. */
  create(init: Pick<Project, 'workdir' | 'request' | 'backend' | 'model'>): Project {
    const id = `p-${randomBytes(4).toString('hex')}`;
    const project: Project = {
      id, created: new Date().toISOString(), ...init, workdir: init.workdir || join(this.home, 'workspaces', id),
      status: 'idle', error: null, questions: [], tasks: [], inbox: [], sessions: {},
    };
    mkdirSync(project.workdir, { recursive: true });
    mkdirSync(join(this.dir(id), 'calls'), { recursive: true });
    this.save(project);
    return project;
  }

  load(id: string): Project {
    const { question, ...project } = JSON.parse(readFileSync(join(this.dir(id), 'project.json'), 'utf8')) as Project & { question?: Question | null };
    // A record from before there could be several questions holds its one as `question`.
    return { ...project, questions: project.questions ?? (question ? [question] : []) };
  }

  save(project: Project): void {
    const path = join(this.dir(project.id), 'project.json');
    writeFileSync(`${path}.${process.pid}.tmp`, JSON.stringify(project, null, 2) + '\n');
    replace(`${path}.${process.pid}.tmp`, path);
  }

  append(id: string, event: FlowEvent): void {
    const path = join(this.dir(id), 'events.jsonl');
    // A line cut short (a crash, a full disk) is ended first, so it alone is lost, not the line after it.
    appendFileSync(path, `${endsLine(path) ? '' : '\n'}${JSON.stringify({ ts: new Date().toISOString(), ...event })}\n`);
  }

  list(): Project[] {
    let ids: string[];
    try { ids = readdirSync(join(this.home, 'projects')); } catch { return []; }
    return ids.filter(id => PROJECT_ID.test(id)).map(id => this.load(id)).sort((a, b) => a.created.localeCompare(b.created));
  }
}

/** Whether a file is missing, empty, or ends its last line. */
function endsLine(path: string): boolean {
  let file: number;
  try { file = openSync(path, 'r'); } catch { return true; }
  try {
    const { size } = fstatSync(file);
    const last = Buffer.alloc(1);
    return size === 0 || readSync(file, last, 0, 1, size - 1) === 1 && last[0] === 0x0a;
  } finally { closeSync(file); }
}

const pause = new Int32Array(new SharedArrayBuffer(4));

/** Rename `from` over `to`. Windows refuses while another program (a virus scanner, an indexer)
 * has `to` open, which lasts a moment: it is tried again for up to two seconds. */
function replace(from: string, to: string): void {
  for (let waited = 0; ; waited += 20) {
    try { renameSync(from, to); return; } catch (error) {
      const busy = ['EPERM', 'EACCES', 'EBUSY'].includes((error as NodeJS.ErrnoException).code ?? '');
      if (process.platform !== 'win32' || !busy || waited >= 2_000) throw error;
      Atomics.wait(pause, 0, 0, 20);
    }
  }
}

function ownerPid(path: string): number | null {
  try { return (JSON.parse(readFileSync(path, 'utf8')) as { pid: number }).pid; } catch { return null; }
}

function alive(pid: number): boolean {
  try { process.kill(pid, 0); return true; } catch (error) { return (error as NodeJS.ErrnoException).code === 'EPERM'; }
}

/** Who asked a question, which names it: an asker has one open at most, since asking holds them. */
export const asker = ({ role, task }: { role: string; task?: string | null }): string => task ? `${role}-${task}` : role;
