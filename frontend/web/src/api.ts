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
  ProjectRow,
  RequestUsage,
  Role,
  Snapshot,
} from '../../core/src/types';
import { ensureResponseOk } from '../../core/src/http';

export type {
  ArtifactInfo,
  BacklogItem,
  Daemon,
  EventMsg,
  ProjectRow,
  RequestUsage,
  Role,
  Snapshot,
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
  backend_label: string;
  model: string;
  effort: string | null;
}
export interface ConfigSnapshot {
  roles: ConfigRole[];
  [k: string]: unknown;
}
export interface Turn {
  ts: number;
  role: string; // "operator" | "argus"
  text: string;
}

const token = (): string | null =>
  new URLSearchParams(window.location.search).get('token') ||
  localStorage.getItem('argus_web_token');

function authHeaders(): Record<string, string> {
  const t = token();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: authHeaders() });
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

async function getBlob(path: string): Promise<Blob> {
  const r = await fetch(path, { headers: authHeaders() });
  await ensureResponseOk(r, 'GET', path);
  return r.blob();
}

const P = (sid: string, path = '') => `/api/projects/${encodeURIComponent(sid)}${path}`;

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
  listProjects: () => getJson<{ projects: ProjectRow[] }>('/api/projects').then((r) => r.projects),
  /** Create a brand-new daemon armed with an objective, and spawn it. */
  createDaemon: (objective: string, name = '') =>
    postJson<{ sid: string; rc: number; daemon: Daemon; objective: string }>('/api/daemons', { objective, name }),
  snapshot: (sid: string) => getJson<Snapshot>(P(sid, '/snapshot?compact=true&events_limit=1')),
  status: (sid: string) => getJson<StatusView>(P(sid, '/status')),
  journal: (sid: string, n = 20) =>
    getJson<{ journal: JournalEntry[] }>(P(sid, `/journal?n=${n}`)).then((r) => r.journal),
  doctor: (sid: string) => getJson<DoctorReport>(P(sid, '/doctor')),
  config: (sid: string) => getJson<ConfigSnapshot>(P(sid, '/config')),
  identity: (sid: string) => getJson<{ identity: string }>(P(sid, '/identity')).then((r) => r.identity),
  transcript: (sid: string, n = 30) =>
    getJson<{ turns: Turn[] }>(P(sid, `/transcript?n=${n}`)).then((r) => r.turns),
  events: (sid: string, limit = 80) =>
    getJson<{ events: EventMsg[] }>(P(sid, `/events?limit=${limit}`)).then((r) => r.events),
  backlogItem: (sid: string, id: string) =>
    getJson<{ item: BacklogItem }>(P(sid, `/backlog/${encodeURIComponent(id)}`)).then((r) => r.item),
  artifacts: (sid: string) =>
    getJson<{ artifacts: ArtifactInfo[] }>(P(sid, '/artifacts')).then((r) => r.artifacts),
  artifact: (sid: string, path: string) => {
    const q = new URLSearchParams({ path });
    return getJson<ArtifactInfo>(P(sid, `/artifact?${q}`));
  },
  artifactBlob: (sid: string, path: string, download = false) => {
    const q = new URLSearchParams({ path });
    if (download) q.set('download', 'true');
    return getBlob(P(sid, `/artifact/raw?${q}`));
  },

  addTask: (sid: string, text: string) =>
    postJson<{ item: BacklogItem }>(P(sid, '/tasks'), { text }).then((r) => r.item),
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
  disposeBacklog: (sid: string, id: string, op: 'done' | 'skip' | 'rm') =>
    postJson(P(sid, `/backlog/${encodeURIComponent(id)}/dispose`), { op }),
  stopBacklog: (sid: string, id: string) => postJson(P(sid, `/backlog/${encodeURIComponent(id)}/stop`)),
  setContinuous: (sid: string, enabled: boolean, objective = '') =>
    postJson(P(sid, '/continuous'), { enabled, objective }),
  startDaemon: (sid: string) => postJson(P(sid, '/daemon/start')),
  stopDaemon: (sid: string, drain = false) => postJson(P(sid, '/daemon/stop'), { drain }),
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
