/**
 * Browser client for the argus webapi — same surface as the terminal client
 * (frontend/tui/src/api.ts) but using the browser fetch + native WebSocket.
 * URLs are relative so Vite proxies /api in dev and the API serves it in prod.
 */

import type {
  ArtifactInfo,
  BacklogItem,
  Daemon,
  EventMsg,
  GitDiffView,
  ProjectRow,
  ProjectCostRow,
  RequestUsage,
  Role,
} from '../../core/src/types';
import { ensureResponseOk } from '../../core/src/http';
import {
  requireCompatibleApiMeta,
  requireSnapshotContract,
  type ApiMeta,
} from '../../core/src/protocol';

export type {
  ArtifactInfo,
  BacklogItem,
  CostControlSnapshot,
  Daemon,
  EventMsg,
  GitDiffView,
  ProjectRow,
  ProjectCostRow,
  RequestUsage,
  Role,
  Snapshot,
  UsageSummary,
} from '../../core/src/types';

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
  backend: string;
  backend_label: string;
  backend_source: string;
  model: string;
  model_source: string;
  reasoning_effort: string | null;
  reasoning_effort_source: string;
  description: string;
}
export interface ConfigKnob {
  name: string;
  group: string;
  value: string;
  source: string;
  default: string;
  doc: string;
}
export interface ConfigSnapshot {
  schema_version: number;
  generated_at_utc: string;
  roles: ConfigRole[];
  operator_knobs: ConfigKnob[];
  how_to_change: string[];
}
export interface Turn {
  ts: number;
  role: string; // "operator" | "argus"
  text: string;
}
export interface ProjectIndex {
  projects: ProjectRow[];
  local_cwd: string;
}
export interface ProjectCostIndex {
  projects: ProjectCostRow[];
  generated_at: number;
}
export interface PlanPreview {
  steps: Array<{ title: string; detail?: string }>;
  notes: string[];
  error: string;
}
export interface TrashEntry {
  trash_id: string;
  sid: string;
  label: string;
  launch_cwd: string;
  trash_path: string;
  trashed_at: number;
}
export interface MetricsSnapshot {
  schema_version?: number;
  slo?: { status?: string; [key: string]: unknown };
  web?: Record<string, unknown>;
  provider?: Record<string, unknown>;
  daemon_commands?: Record<string, unknown>;
  event_validation_failures?: number;
  cost_control?: Record<string, unknown>;
  [key: string]: unknown;
}

const token = (): string | null =>
  new URLSearchParams(window.location.search).get('token') ||
  localStorage.getItem('argus_web_token');

function authHeaders(): Record<string, string> {
  const t = token();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(path, { headers: authHeaders(), signal });
  await ensureResponseOk(r, 'GET', path);
  return (await r.json()) as T;
}

async function postJson<T = Record<string, unknown>>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  await ensureResponseOk(r, 'POST', path);
  return (await r.json()) as T;
}

function requireDaemonCommand<T>(result: T): T {
  const row = result && typeof result === 'object'
    ? result as Record<string, unknown>
    : {};
  const status = String(row.command_status ?? '');
  if (Number(row.rc ?? 0) !== 0 || status === 'failed' || status === 'rejected') {
    throw new Error(String(row.error || `daemon command ${status || 'failed'}`));
  }
  return result;
}

async function mutationJson<T>(
  method: 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  await ensureResponseOk(r, method, path);
  return (await r.json()) as T;
}

async function getBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const r = await fetch(path, { headers: authHeaders(), signal });
  await ensureResponseOk(r, 'GET', path);
  return r.blob();
}

const P = (sid: string, path = '') => `/api/projects/${encodeURIComponent(sid)}${path}`;
const commandId = (): string => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
let apiMetaPromise: Promise<ApiMeta> | undefined;

export function compatibleApiMeta(): Promise<ApiMeta> {
  if (!apiMetaPromise) {
    const request = (async () => {
      const path = '/api/meta';
      const r = await fetch(path, { headers: authHeaders() });
      if (r.status === 404) {
        throw new Error('incompatible Argus API: service does not expose /api/meta');
      }
      await ensureResponseOk(r, 'GET', path);
      return requireCompatibleApiMeta(await r.json());
    })();
    apiMetaPromise = request;
    void request.catch(() => {
      if (apiMetaPromise === request) apiMetaPromise = undefined;
    });
  }
  return apiMetaPromise;
}

/** One decoded SSE frame from the streaming Manager endpoint. */
export interface SSEFrame {
  type: string; // phase | delta | done | error
  [k: string]: unknown;
}

/** The final ``done`` frame payload — same shape as blocking ``message()``. */
export interface StreamDone {
  kind?: string;
  reply?: string | null;
  item?: BacklogItem | null;
  [k: string]: unknown;
}

/**
 * Parse whole SSE frames out of an accumulating buffer (blank-line separated;
 * each ``data:`` line is one JSON object). Returns the frames plus the
 * unconsumed tail. Pure + no I/O so the protocol is unit-testable.
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

export const api = {
  meta: compatibleApiMeta,
  projectIndex: async () => {
    await compatibleApiMeta();
    return getJson<ProjectIndex>('/api/projects');
  },
  listProjects: async () => {
    await compatibleApiMeta();
    return getJson<ProjectIndex>('/api/projects').then((result) => result.projects);
  },
  projectCosts: async (signal?: AbortSignal) => {
    await compatibleApiMeta();
    return getJson<ProjectCostIndex>('/api/projects/costs', signal);
  },
  /** Create a session. The UI arms an optional campaign separately. */
  createDaemon: async (
    objective: string,
    name = '',
    launchCwd = '',
    expectedRevision?: number,
  ) => {
    const path = '/api/daemons';
    const body = {
      objective,
      name,
      launch_cwd: launchCwd,
      command_id: commandId(),
      expected_revision: expectedRevision,
    };
    const send = () => fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
      cache: 'no-store',
    });
    let response = await send();
    if (
      response.status === 400
      && /Invalid HTTP request received/i.test(await response.clone().text())
    ) {
      response = await send();
    }
    await ensureResponseOk(response, 'POST', path);
    return (await response.json()) as {
      sid: string;
      rc: number;
      daemon: Daemon;
      objective: string;
      workdir: string;
    };
  },
  updateProject: (sid: string, name: string) =>
    mutationJson<{ ok: boolean; sid: string; name: string }>('PATCH', P(sid), { name }),
  deleteProject: (sid: string) =>
    mutationJson<{ ok: boolean; sid: string; trash_path: string }>('DELETE', P(sid)),
  snapshot: async (sid: string, signal?: AbortSignal) => {
    await compatibleApiMeta();
    const value = await getJson<unknown>(
      P(sid, '/snapshot?compact=true&events_limit=1'),
      signal,
    );
    return requireSnapshotContract(value);
  },
  status: (sid: string, signal?: AbortSignal) =>
    getJson<StatusView>(P(sid, '/status'), signal),
  journal: (sid: string, n = 20, signal?: AbortSignal) =>
    getJson<{ journal: JournalEntry[] }>(P(sid, `/journal?n=${n}`), signal)
      .then((r) => r.journal),
  doctor: (sid: string, signal?: AbortSignal) =>
    getJson<DoctorReport>(P(sid, '/doctor'), signal),
  config: (sid: string, signal?: AbortSignal) =>
    getJson<ConfigSnapshot>(P(sid, '/config'), signal),
  identity: (sid: string, signal?: AbortSignal) =>
    getJson<{ identity: string }>(P(sid, '/identity'), signal).then((r) => r.identity),
  transcript: (sid: string, n = 30, signal?: AbortSignal) =>
    getJson<{ turns: Turn[] }>(P(sid, `/transcript?n=${n}`), signal)
      .then((r) => r.turns),
  events: (sid: string, limit = 80, signal?: AbortSignal) =>
    getJson<{ events: EventMsg[] }>(
      P(sid, `/events?limit=${limit}&view=ui`),
      signal,
    )
      .then((r) => r.events),
  backlogItem: (sid: string, id: string, signal?: AbortSignal) =>
    getJson<{ item: BacklogItem }>(
      P(sid, `/backlog/${encodeURIComponent(id)}`),
      signal,
    ).then((r) => r.item),
  artifacts: (sid: string, signal?: AbortSignal) =>
    getJson<{ artifacts: ArtifactInfo[] }>(P(sid, '/artifacts'), signal)
      .then((r) => r.artifacts),
  artifact: (sid: string, path: string, signal?: AbortSignal) => {
    const q = new URLSearchParams({ path });
    return getJson<ArtifactInfo>(P(sid, `/artifact?${q}`), signal);
  },
  artifactBlob: (
    sid: string,
    path: string,
    download = false,
    signal?: AbortSignal,
  ) => {
    const q = new URLSearchParams({ path });
    if (download) q.set('download', 'true');
    return getBlob(P(sid, `/artifact/raw?${q}`), signal);
  },
  gitDiff: (sid: string, signal?: AbortSignal) =>
    getJson<GitDiffView>(P(sid, '/git-diff'), signal),
  metrics: (signal?: AbortSignal) =>
    getJson<MetricsSnapshot>('/api/metrics', signal),
  trash: (query = '', limit = 100, offset = 0, signal?: AbortSignal) => {
    const params = new URLSearchParams({
      query,
      limit: String(limit),
      offset: String(offset),
    });
    return getJson<{ entries: TrashEntry[]; total: number }>(
      `/api/trash?${params}`,
      signal,
    );
  },
  restoreTrash: (trashId: string) =>
    postJson<{ ok: boolean; sid: string }>(`/api/trash/${encodeURIComponent(trashId)}/restore`),

  addTask: (sid: string, text: string) =>
    postJson<{ item: BacklogItem }>(P(sid, '/tasks'), { text }).then((r) => r.item),
  abortMission: (sid: string, reason: string) =>
    postJson<{ requested: boolean; item_id: string | null; message: string }>(
      P(sid, '/mission/abort'),
      { reason },
    ),
  answerPending: (sid: string, itemId: string, text: string) =>
    postJson<{
      answered_item_id: string;
      item: BacklogItem;
      daemon?: { rc?: number; error?: string; admission_required?: boolean };
    }>(
      P(sid, `/backlog/${encodeURIComponent(itemId)}/answer`),
      { text },
    ),
  /** The Manager front-door: NL message → chat reply or an enqueued mission. */
  message: (sid: string, text: string, signal?: AbortSignal) =>
    postJson<{ kind: 'chat' | 'task' | 'error'; reply: string | null; item?: BacklogItem | null; daemon_alive?: boolean }>(
      P(sid, '/message'),
      { text },
      signal,
    ),
  /**
   * Streaming Manager front-door (SSE): ``onPhase`` per real step, ``onDelta``
   * per reply block as it's produced, ``onDone`` with the final classification,
   * ``onError`` on failure. Un-freezes the UI — Argus visibly thinks and the
   * answer types in. Fall back to blocking ``message()`` at the call site.
   */
  messageStream: async (
    sid: string,
    text: string,
    handlers: {
      onPhase?: (label: string, role: string) => void;
      onDelta?: (block: string, messageId: string) => void;
      onDone?: (result: StreamDone) => void;
      onError?: (err: Error) => void;
    },
    signal?: AbortSignal,
  ): Promise<void> => {
    const res = await fetch(P(sid, '/message/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ text }),
      signal,
    });
    await ensureResponseOk(res, 'POST', P(sid, '/message/stream'));
    if (!res.body) throw new Error('Manager stream returned no response body');
    const dispatch = (f: SSEFrame) => {
      if (signal?.aborted) return;
      if (f.type === 'phase') handlers.onPhase?.(String(f.label ?? ''), String(f.role ?? 'manager'));
      else if (f.type === 'delta') handlers.onDelta?.(String(f.text ?? ''), String(f.message_id ?? ''));
      else if (f.type === 'done') handlers.onDone?.((f.result ?? {}) as StreamDone);
      else if (f.type === 'error') handlers.onError?.(new Error(String(f.error ?? 'stream error')));
    };
    const reader = res.body.getReader();
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
    if (!signal?.aborted) parseSSEFrames(buf + '\n\n').frames.forEach(dispatch);
  },
  nudge: (sid: string, text: string) => postJson(P(sid, '/nudge'), { text }),
  note: (sid: string, text: string) => postJson(P(sid, '/note'), { text }),
  previewPlan: (sid: string, text: string) =>
    postJson<PlanPreview>(P(sid, '/plan'), { text }),
  setConfig: (sid: string, name: string, value: string) =>
    postJson<Record<string, unknown>>(P(sid, '/config/set'), { name, value }),
  setBudgets: (sid: string, values: Record<string, string>) =>
    postJson<{ values: Record<string, string>; restart_required: boolean }>(
      P(sid, '/config/budget'),
      { values },
    ),
  setIdentity: (sid: string, text: string) =>
    postJson<{ ok: boolean }>(P(sid, '/identity'), { text }),
  resetManager: (sid: string) =>
    postJson<{ ok: boolean }>(P(sid, '/reset')),
  skills: (sid: string, args = 'ls') =>
    postJson<{ text: string }>(P(sid, '/skills'), { args }).then((result) => result.text),
  setLaunchCwd: (sid: string, launchCwd: string) =>
    postJson<{ ok: boolean }>(P(sid, '/launch-cwd'), { launch_cwd: launchCwd }),
  setWorkdir: (sid: string, workdir: string) =>
    postJson<{ ok: boolean; workdir: string; unchanged?: boolean }>(
      P(sid, '/workdir'),
      { workdir },
    ),
  disposeBacklog: (sid: string, id: string, op: 'done' | 'skip' | 'rm') =>
    postJson(P(sid, `/backlog/${encodeURIComponent(id)}/dispose`), { op }),
  stopBacklog: (sid: string, id: string) => postJson(P(sid, `/backlog/${encodeURIComponent(id)}/stop`)),
  setContinuous: (sid: string, enabled: boolean, objective = '') =>
    postJson(P(sid, '/continuous'), { enabled, objective }),
  startDaemon: (sid: string, expectedRevision?: number) => postJson(P(sid, '/daemon/start'), {
    command_id: commandId(),
    expected_revision: expectedRevision,
  }).then(requireDaemonCommand),
  stopDaemon: (sid: string, drain = false, expectedRevision?: number) => postJson(P(sid, '/daemon/stop'), {
    drain,
    command_id: commandId(),
    expected_revision: expectedRevision,
  }).then(requireDaemonCommand),
  replaceDaemon: (sid: string, victimSid: string, resumeContinuous = false, expectedRevision?: number) =>
    postJson(P(sid, '/daemon/replace'), {
      victim_sid: victimSid,
      resume_continuous: resumeContinuous,
      command_id: commandId(),
      expected_revision: expectedRevision,
    }).then(requireDaemonCommand),
  upgradeDaemon: (sid: string, expectedRevision?: number) =>
    postJson(P(sid, '/daemon/upgrade'), {
      command_id: commandId(),
      expected_revision: expectedRevision,
    }).then(requireDaemonCommand),
};

/** Open the live event stream for a project. Returns a close() fn. */
export function openStream(
  sid: string,
  onEvent: (ev: EventMsg) => void,
  opts: { replay?: number; onOpen?: () => void; onClose?: () => void } = {},
): () => void {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const q = new URLSearchParams();
  if (opts.replay != null) q.set('replay', String(opts.replay));
  q.set('view', 'ui');
  const t = token();
  if (t) q.set('token', t);
  const url = `${proto}//${window.location.host}${P(sid, '/stream')}?${q}`;
  let ws: WebSocket | null = null;
  let closed = false;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const connect = () => {
    if (closed) return;
    ws = new WebSocket(url);
    ws.onopen = () => opts.onOpen?.();
    ws.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data as string) as EventMsg;
        if (ev && typeof ev === 'object') onEvent(ev);
      } catch {
        /* ignore malformed frame */
      }
    };
    ws.onclose = () => {
      opts.onClose?.();
      if (!closed) retry = setTimeout(connect, 1000); // reconnect with backoff
    };
    ws.onerror = () => ws?.close();
  };
  connect();
  return () => {
    closed = true;
    if (retry) clearTimeout(retry);
    ws?.close();
  };
}
