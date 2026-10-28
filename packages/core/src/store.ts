// The state root: one process owns it (owner.json), and each project is a directory with its
// current state (project.json, replaced atomically) and its history (events.jsonl, appended).
// A fact lives in project.json only; events record changes and are never read back as state.

import { appendFileSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import type { Backend, Role, Usage } from './types.js';

export type Status = 'working' | 'asking' | 'failed' | 'idle' | 'done';
/** `returned`: handed to the Planner or Manager, closed for the team; `retired` by the Planner. */
export type TaskStatus = 'open' | 'done' | 'returned' | 'retired';
export type Holder = 'engineer' | 'reviewer';

export interface Ticket {
  goal: string;
  acceptance: string[];
  constraints: string[];
  context: string[];
  standing: boolean;
  /** The task a direct ticket became; its acceptance completes a bounded ticket. */
  task: string | null;
}

export interface Note { from: Role | 'operator'; text: string }

export interface Task {
  id: string;
  title: string;
  /** Empty for a direct ticket's task: the ticket is its brief. */
  objective: string;
  deps: string[];
  review: boolean;
  status: TaskStatus;
  holder: Holder;
  /** What the holder reads next: the last hand-off's note, or the operator's answer. */
  note: Note | null;
}

export interface Question {
  role: Role;
  task: string | null;
  question: string;
  options: { label: string; description?: string }[];
}

export interface Project {
  id: string;
  created: string;
  workdir: string;
  request: string;
  backend: Backend;
  model: string | null;
  status: Status;
  /** Why the last call failed, with status 'failed'. */
  error: string | null;
  /** The open question, with status 'asking'. */
  question: Question | null;
  ticket: Ticket | null;
  tasks: Task[];
  /** What the Planner reads at its next turn; it is called when no task is ready and this is not empty. */
  inbox: string[];
  /** Backend session per role: manager, planner, <task>.engineer, <task>.reviewer. */
  sessions: Record<string, string>;
}

export type FlowEvent =
  | { type: 'message'; from: 'operator' | 'manager'; text: string }
  | { type: 'call.start'; role: Role; task?: string }
  | { type: 'call.end'; role: Role; task?: string; callId: string; threadId: string | null; completed: boolean;
      error: string | null; model: string | null; usage: Usage | null; ms: number }
  | { type: 'handoff'; role: Role; task?: string; to: string; summary: string; fields: Record<string, unknown> }
  | { type: 'task.added'; task: Task }
  | { type: 'task.settled'; task: string; status: TaskStatus }
  | { type: 'status'; status: Status; error?: string; question?: Question };

export class Store {
  constructor(readonly home = process.env.ARGUS_HOME || join(homedir(), '.argus')) {}

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

  create(init: Pick<Project, 'workdir' | 'request' | 'backend' | 'model'>): Project {
    const project: Project = {
      id: `p-${randomBytes(4).toString('hex')}`, created: new Date().toISOString(), ...init,
      status: 'working', error: null, question: null, ticket: null, tasks: [], inbox: [], sessions: {},
    };
    mkdirSync(join(this.dir(project.id), 'calls'), { recursive: true });
    this.save(project);
    return project;
  }

  load(id: string): Project {
    return JSON.parse(readFileSync(join(this.dir(id), 'project.json'), 'utf8')) as Project;
  }

  save(project: Project): void {
    const path = join(this.dir(project.id), 'project.json');
    writeFileSync(`${path}.${process.pid}.tmp`, JSON.stringify(project, null, 2) + '\n');
    renameSync(`${path}.${process.pid}.tmp`, path);
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

function ownerPid(path: string): number | null {
  try { return (JSON.parse(readFileSync(path, 'utf8')) as { pid: number }).pid; } catch { return null; }
}

function alive(pid: number): boolean {
  try { process.kill(pid, 0); return true; } catch (error) { return (error as NodeJS.ErrnoException).code === 'EPERM'; }
}
