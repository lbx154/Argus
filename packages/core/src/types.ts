// The boundary between the flow (roles, tasks, state) and the agent layer (backend CLIs,
// the hand-off form, the sandbox). The flow builds an AgentRequest; the agent layer runs one
// backend turn and returns what the role handed off.

export const BACKENDS = ['codex', 'claude', 'copilot', 'pi'] as const;
export type Backend = (typeof BACKENDS)[number];
/** The backends whose CLI stays open between a session's turns (agent/session.ts); the others start one each turn. */
export const KEPT: readonly Backend[] = ['codex', 'claude'];
export const ROLES = ['manager', 'planner', 'engineer', 'reviewer'] as const;
export type Role = (typeof ROLES)[number];
/** The hand-off form a role's turn ends with (agent/handoff.ts). */
export type Form = 'manager' | 'plan' | 'engineer' | 'review';
/** What the turn may do to the workdir: read it, or write it. */
export type Access = 'read' | 'write';

export interface AgentRequest {
  backend: Backend;
  role: Role;
  form: Form;
  prompt: string;
  /** The project's workdir; the CLI runs there. */
  cwd: string;
  access: Access;
  /** Further directories the turn may write (Access 'write') or read. */
  writeDirs?: string[];
  readDirs?: string[];
  model?: string;
  effort?: string;
  /** Continue this backend session instead of starting a new one. */
  resume?: string;
  /** The key the session's CLI runs under between turns, till CallAgent.close ends it; without
   * one, the CLI ends with the turn. */
  session?: string;
  /** The role's standing instructions: its rules, and where things are. A backend with a place for
   * them is given them with each CLI it starts, so they outlast its compaction of the session; the
   * others read them at the head of a session's first prompt. */
  instructions?: string;
  /** Who gave the task the turn is on: only the task's giver is offered among those who hear how it ended. */
  giver?: 'manager' | 'planner';
  /** Told of each step the role takes as its turn goes on. */
  onStep?: (step: Step) => void;
  /** Where the call's raw output and usage go. */
  callsDir: string;
  usageFile: string;
  signal?: AbortSignal;
}

/** What a role does as its turn goes on, as the operator follows it: what it says, a command it
 * runs, a file it changes. */
export interface Step { kind: 'message' | 'command' | 'edit'; text: string }

export interface HandOff {
  form: Form;
  /** Who holds the work next; the only part the host routes by. */
  to: string;
  /** Free text for them, passed on as written; the operator reads it too. One letter, except
   * when the Planner opens several tasks at once: one letter each. */
  letters: string[];
  /** For each of the Planner's letters to the Engineer, the open task it is for, or the tasks the
   * task it opens waits for. */
  tasks?: { task: string; after: string[] }[];
  /** The files of the result the letter hands over, the first to open first. */
  open?: string[];
}

/** A token count as a backend reports it: a whole number, 0 when missing. */
export const count = (value: unknown): number => typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.trunc(value) : 0;

export interface Usage {
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  reasoning_output_tokens: number;
  cost_usd: number | null;
}

export interface AgentResult {
  callId: string;
  /** The backend session id, to resume this role later. */
  threadId: string | null;
  /** The backend reported a completed turn. */
  completed: boolean;
  /** Why the turn did not complete, in the backend's words; for a completed turn, why its form was refused. */
  error: string | null;
  /** The form the turn ended with, once accepted. */
  handoff: HandOff | null;
  model: string | null;
  usage: Usage | null;
  ms: number;
}

/** One backend turn. `close` ends the sessions kept under keys that start with its prefix, and all
 * their CLIs started; `steer` puts a letter into the turn under way in a session, false when none
 * is or its backend cannot take one; `models` names the models a backend offers, its default marked. */
export type CallAgent = ((request: AgentRequest) => Promise<AgentResult>) & {
  close?: (prefix: string) => Promise<void>;
  steer?: (session: string, text: string) => Promise<boolean>;
  models?: (backend: Backend, cwd: string) => Promise<{ model: string; default: boolean }[]>;
};
