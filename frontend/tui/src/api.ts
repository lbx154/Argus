import WebSocket from 'ws';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { parse, resolve } from 'node:path';
import type {
  ArtifactInfo,
  BacklogItem,
  Daemon,
  DaemonAdmission,
  EventMsg,
  ProjectRow,
  RequestUsage,
  Role,
  Snapshot,
  UsageSummary,
} from '../../core/src/types.js';
import { ensureResponseOk } from '../../core/src/http.js';
import {
  requireCompatibleApiMeta,
  requireSnapshotContract,
  type ApiMeta,
} from '../../core/src/protocol.js';

export type {
  ArtifactInfo,
  BacklogItem,
  CostControlSnapshot,
  Daemon,
  DaemonAdmission,
  EventMsg,
  ProjectRow,
  RequestUsage,
  Role,
  Snapshot,
  UsageSummary,
} from '../../core/src/types.js';

/**
 * Client for the argus-skill webapi (argus_skill/webapi/server.py). ALL network
 * logic lives here so the render layer stays a thin, testable shell: events over
 * WebSocket (/stream), snapshots + commands over REST. See M0/M1 endpoints.
 */

export interface JournalEntry {
  id: string;
  ts: number;
  kind: string;
  title: string;
  summary: string;
  tags: string[];
  cost_usd?: number;
  extra?: Record<string, unknown>;
}

export function defaultExecutionWorkdir(
  launchCwd: string,
  home = homedir(),
): string | undefined {
  const resolvedLaunch = resolve(launchCwd);
  if (resolvedLaunch === resolve(home) || resolvedLaunch === parse(resolvedLaunch).root) {
    return undefined;
  }
  return resolvedLaunch;
}

export interface StatusView {
  identity: string;
  backlog_pending: BacklogItem[];
  pending_questions: Array<Record<string, unknown>>;
  journal: JournalEntry[];
  continuous: { enabled: boolean; objective: string; done_reason?: string; done_at?: string };
  inbox_pending: number;
  daemon: Daemon;
  roles: Role[];
  active_role: string | null;
  request_usage?: RequestUsage;
}

export interface DoctorCheck {
  name: string;
  ok: boolean;
  detail: string;
  fix: string;
}

export interface DoctorReport {
  checks: DoctorCheck[];
  recommended: DoctorCheck | null;
  log_tail: string;
}

export interface ConfigRole {
  role: string;
  backend_label: string;
  model: string;
  effort: string | null;
}

export interface ConfigSnapshot {
  roles: ConfigRole[];
  [k: string]: unknown;
}

export interface Turn {
  role: string;
  text: string;
  ts?: number;
}

export interface ApiOptions {
  host: string;
  port: number;
  project: string;
  token?: string;
  onCompatibilityWarning?: (warning: string) => void;
}

export interface CreatedDaemon {
  sid: string;
  rc: number;
  spawned: boolean;
  daemon: Daemon;
  objective: string;
  start?: DaemonStartResult;
  command_id?: string;
  command_status?: string;
  command_revision?: number;
}

export interface PlanPreview {
  steps: Array<{ title: string; detail?: string }>;
  notes: string[];
  error: string;
}

/** A Manager-authored restatement of an operator draft (see /rewrite). */
export interface PromptRewrite {
  original: string;
  rewritten: string;
  changes: string[];
  questions: string[];
  error: string;
}

export interface DaemonStartResult extends Partial<DaemonAdmission> {
  rc: number;
  already_alive?: boolean;
  error?: string;
  daemon?: Daemon;
  admission_required?: boolean;
  limit?: number;
  active_count?: number;
  running_daemons?: ProjectRow[];
  parked_session?: string;
  parked_state?: string;
  command_id?: string;
  command_status?: string;
  command_revision?: number;
}

/** One decoded SSE frame from the streaming Manager endpoint. */
export interface SSEFrame {
  type: string; // phase | delta | done | error
  [k: string]: unknown;
}

export interface ManagerPhaseMeta {
  heartbeat: boolean;
  quietS: number;
  /** Progress kind ('command_execution', 'tool_use', …) when the backend sent one. */
  kind: string;
  /** Longer redacted body for the step (e.g. the full command). */
  detail: string;
}

/** The final ``done`` frame's payload — the same shape blocking ``message()`` returns. */
export interface StreamDone {
  kind?: string;
  reply?: string | null;
  item?: BacklogItem | null;
  daemon?: DaemonStartResult;
  [k: string]: unknown;
}

export function taskDispatchMessage(result: StreamDone): string {
  const title = result.item?.title || 'new mission';
  if (result.daemon?.admission_required) {
    return `→ queued: choose one running session to park before starting ${title}`;
  }
  if (result.daemon && result.daemon.rc !== 0) {
    return `→ queued but not running: ${result.daemon.error || 'background executor failed to start'}`;
  }
  return `→ dispatched to the team: ${title}`;
}

/**
 * Parse whole SSE frames out of an accumulating buffer. Frames are separated by
 * a blank line; each ``data:`` line carries one JSON object. Returns the decoded
 * frames plus the unconsumed tail (a partial frame still arriving). Pure + no
 * I/O so the streaming protocol is unit-testable without a socket.
 */
export function parseSSEFrames(buf: string): { frames: SSEFrame[]; rest: string } {
  const frames: SSEFrame[] = [];
  let idx: number;
  while ((idx = buf.indexOf('\n\n')) >= 0) {
    const raw = buf.slice(0, idx);
    buf = buf.slice(idx + 2);
    for (const line of raw.split('\n')) {
      const l = line.trim();
      if (l.startsWith('data:')) {
        try {
          frames.push(JSON.parse(l.slice(5).trim()) as SSEFrame);
        } catch {
          /* ignore a malformed frame */
        }
      }
    }
  }
  return { frames, rest: buf };
}

export class ApiClient {
  readonly httpBase: string;
  readonly wsBase: string;
  readonly project: string;
  private readonly token?: string;
  private readonly onCompatibilityWarning?: (warning: string) => void;
  private metaPromise?: Promise<ApiMeta>;

  constructor(opts: ApiOptions) {
    this.httpBase = `http://${opts.host}:${opts.port}`;
    this.wsBase = `ws://${opts.host}:${opts.port}`;
    this.project = opts.project;
    this.token = opts.token;
    this.onCompatibilityWarning = opts.onCompatibilityWarning;
  }

  private authHeaders(): Record<string, string> {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {};
  }

  private p(path: string): string {
    return `${this.httpBase}/api/projects/${encodeURIComponent(this.project)}${path}`;
  }

  meta(): Promise<ApiMeta> {
    if (!this.metaPromise) {
      const request = (async () => {
        const path = '/api/meta';
        const r = await fetch(`${this.httpBase}${path}`, { headers: this.authHeaders() });
        if (r.status === 404) {
          throw new Error('incompatible Argus API: service does not expose /api/meta');
        }
        await ensureResponseOk(r, 'GET', path);
        return requireCompatibleApiMeta(
          await r.json(),
          this.onCompatibilityWarning,
        );
      })();
      this.metaPromise = request;
      void request.catch(() => {
        if (this.metaPromise === request) this.metaPromise = undefined;
      });
    }
    return this.metaPromise;
  }

  async listProjects(): Promise<ProjectRow[]> {
    await this.meta();
    const r = await fetch(`${this.httpBase}/api/projects`, { headers: this.authHeaders() });
    await ensureResponseOk(r, 'GET', '/api/projects');
    return ((await r.json()) as { projects: ProjectRow[] }).projects;
  }

  async createDaemon(
    objective = '',
    name = '',
    launchCwd = process.cwd(),
    expectedRevision?: number,
    commandId = randomUUID(),
  ): Promise<CreatedDaemon> {
    const path = '/api/daemons';
    const executionWorkdir = defaultExecutionWorkdir(launchCwd);
    const payload: Record<string, unknown> = {
        objective,
        name,
        launch_cwd: launchCwd,
        command_id: commandId,
        expected_revision: expectedRevision,
    };
    if (executionWorkdir) payload.workdir = executionWorkdir;
    const body = JSON.stringify(payload);
    const send = () => fetch(`${this.httpBase}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Connection: 'close',
        ...this.authHeaders(),
      },
      body,
    });
    let r = await send();
    if (
      r.status === 400
      && /Invalid HTTP request received/i.test(await r.clone().text())
    ) {
      r = await send();
    }
    await ensureResponseOk(r, 'POST', path);
    return (await r.json()) as CreatedDaemon;
  }

  async replaceDaemon(
    victimSid: string,
    resumeContinuous = false,
    expectedRevision?: number,
    commandId = randomUUID(),
  ): Promise<DaemonStartResult> {
    const result = await this.post('/daemon/replace', {
      victim_sid: victimSid,
      resume_continuous: resumeContinuous,
      command_id: commandId,
      expected_revision: expectedRevision,
    });
    return result as unknown as DaemonStartResult;
  }

  async scheduleDaemonUpgrade(
    project: string,
    expectedRevision?: number,
    commandId = randomUUID(),
  ): Promise<Record<string, unknown>> {
    const path = `/api/projects/${encodeURIComponent(project)}/daemon/upgrade-schedule`;
    const r = await fetch(`${this.httpBase}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
      body: JSON.stringify({
        command_id: commandId,
        expected_revision: expectedRevision,
      }),
    });
    await ensureResponseOk(r, 'POST', path);
    return (await r.json()) as Record<string, unknown>;
  }

  async setProjectLaunchCwd(project: string, launchCwd: string): Promise<void> {
    const path = `/api/projects/${encodeURIComponent(project)}/launch-cwd`;
    const r = await fetch(`${this.httpBase}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
      body: JSON.stringify({ launch_cwd: launchCwd }),
    });
    await ensureResponseOk(r, 'POST', path);
  }

  async setProjectWorkdir(project: string, workdir: string): Promise<void> {
    const path = `/api/projects/${encodeURIComponent(project)}/workdir`;
    const r = await fetch(`${this.httpBase}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
      body: JSON.stringify({ workdir }),
    });
    await ensureResponseOk(r, 'POST', path);
  }

  async renameProject(name: string): Promise<{ ok: boolean; sid: string; name: string }> {
    const path = this.p('');
    const r = await fetch(path, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
      body: JSON.stringify({ name }),
    });
    await ensureResponseOk(r, 'PATCH', path);
    return (await r.json()) as { ok: boolean; sid: string; name: string };
  }

  async snapshot(eventsLimit = 1): Promise<Snapshot> {
    await this.meta();
    const r = await fetch(this.p(`/snapshot?compact=true&events_limit=${eventsLimit}`));
    await ensureResponseOk(r, 'GET', '/snapshot');
    return requireSnapshotContract(await r.json());
  }

  async postTask(text: string): Promise<BacklogItem> {
    const r = await fetch(this.p('/tasks'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
      body: JSON.stringify({ text }),
    });
    await ensureResponseOk(r, 'POST', '/tasks');
    return ((await r.json()) as { item: BacklogItem }).item;
  }

  async postNudge(text: string): Promise<void> {
    const r = await fetch(this.p('/nudge'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
      body: JSON.stringify({ text }),
    });
    await ensureResponseOk(r, 'POST', '/nudge');
  }

  /** The Manager front-door: natural language → chat reply or an enqueued mission. */
  async message(
    text: string,
    signal?: AbortSignal,
  ): Promise<StreamDone> {
    const r = await fetch(this.p('/message'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
      body: JSON.stringify({ text }),
      signal,
    });
    await ensureResponseOk(r, 'POST', '/message');
    return (await r.json()) as StreamDone;
  }

  /**
   * Streaming Manager front-door (Server-Sent Events). The reply arrives live —
   * ``onPhase`` for each real step ("Manager · reading events.jsonl"), ``onDelta``
   * for each reply block as it's produced, ``onDone`` with the final
   * classification + reply, ``onError`` on failure. This is what un-freezes the
   * CLI: the operator sees Argus think and the answer type in, instead of a dead
   * screen until the whole turn finishes. Falls back to blocking ``message()``
   * at the call site if the stream can't be opened.
   */
  async messageStream(
    text: string,
    handlers: {
      onPhase?: (label: string, role: string, meta: ManagerPhaseMeta) => void;
      onDelta?: (block: string, messageId: string) => void;
      onDone?: (result: StreamDone) => void;
      onError?: (err: Error) => void;
    },
    signal?: AbortSignal,
  ): Promise<void> {
    const res = await fetch(this.p('/message/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
      body: JSON.stringify({ text }),
      signal,
    });
    await ensureResponseOk(res, 'POST', '/message/stream');
    if (!res.body) throw new Error('Manager stream returned no response body');
    const dispatch = (frame: SSEFrame) => {
      if (signal?.aborted) return;
      if (frame.type === 'phase') {
        const quietS = Number(frame.quiet_s ?? 0);
        handlers.onPhase?.(
          String(frame.label ?? ''),
          String(frame.role ?? 'manager'),
          {
            heartbeat: frame.heartbeat === true,
            quietS: Number.isFinite(quietS) ? quietS : 0,
            kind: String(frame.kind ?? ''),
            detail: String(frame.detail ?? ''),
          },
        );
      }
      else if (frame.type === 'delta') handlers.onDelta?.(String(frame.text ?? ''), String(frame.message_id ?? ''));
      else if (frame.type === 'done') handlers.onDone?.((frame.result ?? {}) as StreamDone);
      else if (frame.type === 'error') handlers.onError?.(new Error(String(frame.error ?? 'stream error')));
    };
    const reader = (res.body as ReadableStream<Uint8Array>).getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parsed = parseSSEFrames(buf);
      buf = parsed.rest;
      parsed.frames.forEach(dispatch);
    }
    // flush any trailing frame without a terminating blank line
    if (!signal?.aborted) parseSSEFrames(buf + '\n\n').frames.forEach(dispatch);
  }

  // ── Wave-1 read/inspect ──
  private async getJson<T>(path: string): Promise<T> {
    const r = await fetch(this.p(path), { headers: this.authHeaders() });
    await ensureResponseOk(r, 'GET', path);
    return (await r.json()) as T;
  }

  private async post(path: string, body?: unknown): Promise<Record<string, unknown>> {
    const r = await fetch(this.p(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    await ensureResponseOk(r, 'POST', path);
    return (await r.json()) as Record<string, unknown>;
  }

  getStatus(): Promise<StatusView> {
    return this.getJson<StatusView>('/status');
  }

  async getJournal(n = 10): Promise<JournalEntry[]> {
    return (await this.getJson<{ journal: JournalEntry[] }>(`/journal?n=${n}`)).journal;
  }

  getDoctor(): Promise<DoctorReport> {
    return this.getJson<DoctorReport>('/doctor');
  }

  getConfig(): Promise<ConfigSnapshot> {
    return this.getJson<ConfigSnapshot>('/config');
  }

  async getIdentity(): Promise<string> {
    return (await this.getJson<{ identity: string }>('/identity')).identity;
  }

  async getTranscript(n = 20): Promise<Turn[]> {
    return (await this.getJson<{ turns: Turn[] }>(`/transcript?n=${n}`)).turns;
  }

  async getArtifacts(): Promise<ArtifactInfo[]> {
    return (await this.getJson<{ artifacts: ArtifactInfo[] }>('/artifacts')).artifacts;
  }

  async getBacklogItem(id: string): Promise<BacklogItem> {
    return (await this.getJson<{ item: BacklogItem }>(`/backlog/${encodeURIComponent(id)}`)).item;
  }

  getArtifact(path: string): Promise<ArtifactInfo> {
    const q = new URLSearchParams({ path });
    return this.getJson<ArtifactInfo>(`/artifact?${q}`);
  }

  async postNote(text: string): Promise<void> {
    await this.post('/note', { text });
  }

  async previewPlan(text: string): Promise<PlanPreview> {
    return (await this.post('/plan', { text })) as unknown as PlanPreview;
  }

  /**
   * Ask the Manager to restate a short draft as an executable brief. Preview
   * only — nothing is queued; the operator edits/sends the result themselves.
   */
  async rewritePrompt(text: string): Promise<PromptRewrite> {
    return (await this.post('/prompt/rewrite', { text })) as unknown as PromptRewrite;
  }

  async setConfig(name: string, value: string): Promise<Record<string, unknown>> {
    return this.post('/config/set', { name, value });
  }

  async setIdentity(text: string): Promise<void> {
    await this.post('/identity', { text });
  }

  async resetManager(): Promise<void> {
    await this.post('/reset');
  }

  async skills(args = 'ls'): Promise<string> {
    return String((await this.post('/skills', { args })).text ?? '');
  }

  async disposeBacklog(id: string, op: 'done' | 'skip' | 'rm'): Promise<BacklogItem> {
    return (await this.post(`/backlog/${encodeURIComponent(id)}/dispose`, { op })).item as BacklogItem;
  }

  async stopBacklog(id: string): Promise<BacklogItem> {
    return (await this.post(`/backlog/${encodeURIComponent(id)}/stop`)).item as BacklogItem;
  }

  async abortMission(reason = ''): Promise<{
    requested: boolean;
    item_id: string | null;
    message: string;
  }> {
    return await this.post('/mission/abort', { reason }) as {
      requested: boolean;
      item_id: string | null;
      message: string;
    };
  }

  /**
   * Open the live event stream. Returns the socket so the caller can close it.
   * The token (if any) rides the query string — browsers/ws can't set WS headers.
   */
  connectStream(handlers: {
    onEvent: (ev: EventMsg) => void;
    onOpen?: () => void;
    onClose?: (code: number) => void;
    onError?: (err: Error) => void;
    replay?: number;
  }): WebSocket {
    const q = new URLSearchParams();
    if (handlers.replay != null) q.set('replay', String(handlers.replay));
    if (this.token) q.set('token', this.token);
    const url = `${this.wsBase}/api/projects/${encodeURIComponent(this.project)}/stream?${q}`;
    const ws = new WebSocket(url);
    ws.on('open', () => handlers.onOpen?.());
    ws.on('message', (data) => {
      try {
        const ev = JSON.parse(String(data)) as EventMsg;
        if (ev && typeof ev === 'object') handlers.onEvent(ev);
      } catch {
        /* ignore malformed frame */
      }
    });
    ws.on('close', (code) => handlers.onClose?.(code));
    ws.on('error', (err) => handlers.onError?.(err as Error));
    return ws;
  }
}
