// The boundary between the flow (roles, tasks, state) and the agent layer (backend CLIs,
// the hand_off tool, the sandbox). The flow builds an AgentRequest; the agent layer runs one
// backend turn and returns what the role handed off.

export type Backend = 'codex' | 'claude';
export type Role = 'manager' | 'planner' | 'engineer' | 'reviewer';
/** A form name in packages/contracts/schemas/handoff_forms.json. */
export type Form = 'manager' | 'plan' | 'engineer' | 'review';
/** What the turn may touch: nothing, read the workdir, or write it. */
export type Access = 'none' | 'read' | 'write';

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
  /** Checks the form's schema cannot express; a returned string rejects the hand-off with that reason. */
  check?: (handoff: HandOff) => string | null;
  /** Where the call's raw output and usage go. */
  callsDir: string;
  usageFile: string;
  signal?: AbortSignal;
}

export interface HandOff {
  form: Form;
  to: string;
  summary: string;
  /** Every other field of the form, as the role filled it. */
  fields: Record<string, unknown>;
}

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
  /** Why the turn did not complete, in the backend's words. */
  error: string | null;
  /** The accepted hand-off, or null when the turn ended without one. */
  handoff: HandOff | null;
  /** The role's last message, shown to the operator; never used for routing. */
  text: string;
  model: string | null;
  usage: Usage | null;
  ms: number;
}

export type CallAgent = (request: AgentRequest) => Promise<AgentResult>;
