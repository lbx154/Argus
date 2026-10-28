// The state root: one process owns it (owner.json), and each project is a directory with its
// current state (project.json, replaced atomically) and its history (events.jsonl, appended).
// A fact lives in project.json only; events record changes and are never read back as state.

import { appendFileSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import type { Backend, Role, Usage } from './types.js';

/** `idle`: no letter is waiting for anyone but the operator. */
export type Status = 'working' | 'asking' | 'failed' | 'idle';
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
}

export interface Question {
  role: Role;
  task: string | null;
  /** The asker's letter. */
  question: string;
}

export interface Project {
  id: string;
  created: string;
  workdir: string;
  request: string;
  backend: Backend;
  model: string | null;
  /** The reasoning effort of every call, in the backend's word for it; absent, the backend's default. */
  effort?: string;
  /** The operator's name for the project; absent, the page names it by its request. */
  name?: string;
  status: Status;
  /** Why the last call failed, with status 'failed'. */
  error: string | null;
  /** The open question, with status 'asking'. */
  question: Question | null;
  tasks: Task[];
  /** Letters to the Planner; it is called when no task is open and one is waiting. */
  inbox: string[];
  /** Letters from the team to the Manager that no Manager turn has taken yet. */
  forManager?: string[];
  /** Backend session per role: manager, planner, <task>.engineer, <task>.reviewer. */
  sessions: Record<string, string>;
}

export type FlowEvent =
  /** The operator's words: to the Manager, or to the role whose question they answer. */
  | { type: 'message'; from: 'operator'; text: string; to?: Role }
  | { type: 'call.start'; role: Role; task?: string }
  | { type: 'call.end'; role: Role; task?: string; callId: string; threadId: string | null; completed: boolean;
      error: string | null; model: string | null; usage: Usage | null; ms: number }
  | { type: 'handoff'; role: Role; task?: string; to: string; letter: string }
  | { type: 'task.added'; task: Task }
  | { type: 'task.closed'; task: string }
  | { type: 'status'; status: Status; error?: string; question?: Question };

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
      status: 'working', error: null, question: null, tasks: [], inbox: [], sessions: {},
    };
    mkdirSync(project.workdir, { recursive: true });
    mkdirSync(join(this.dir(id), 'calls'), { recursive: true });
    this.save(project);
    return project;
  }

  load(id: string): Project {
    return JSON.parse(readFileSync(join(this.dir(id), 'project.json'), 'utf8')) as Project;
  }

  save(project: Project): void {
    const path = join(this.dir(project.id), 'project.json');
    writeFileSync(`${path}.${process.pid}.tmp`, JSON.stringify(project, null, 2) + '\n');
    replace(`${path}.${process.pid}.tmp`, path);
  }

  append(id: string, event: FlowEvent): void {
    appendFileSync(join(this.dir(id), 'events.jsonl'), JSON.stringify({ ts: new Date().toISOString(), ...event }) + '\n');
  }

  list(): Project[] {
    let ids: string[];
    try { ids = readdirSync(join(this.home, 'projects')); } catch { return []; }
    return ids.map(id => this.load(id)).sort((a, b) => a.created.localeCompare(b.created));
  }
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
