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
  Snapshot,
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
  message_id?: string;
  mission_result?: boolean;
  item_id?: string;
  success?: boolean;
  summary?: string;
  delivery_id?: string;
  delivery?: unknown;
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
/** A Manager-authored restatement of an operator draft (see the rewrite button). */
export interface PromptRewrite {
  original: string;
  rewritten: string;
  changes: string[];
  questions: string[];
  error: string;
}
export interface UploadedAttachment {
  attachment_id: string;
  relative_path: string;
  original_name: string;
  stored_name: string;
  mime: string;
  size_bytes: number;
}
export interface MessageAttachmentRef {
  attachment_id: string;
}
export interface AttachmentUploadResponse {
  attachments: UploadedAttachment[];
  limits: {
    max_count: number;
    max_bytes_per_file: number;
    max_total_bytes: number;
  };
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
export interface SourceUpdateStatus {
  schema_version: number;
  state: 'idle' | 'checking' | 'available' | 'current' | 'updating' | 'succeeded' | 'failed';
  phase: string;
  running: boolean;
  source_root: string;
  upstream: string;
  current_revision: string;
  upstream_revision: string;
  branch: string;
  dirty: boolean | null;
  can_update: boolean;
  update_available: boolean | null;
  changed: boolean;
  restart_required: boolean;
  message: string;
  error: string;
  started_at: number | null;
  checked_at: number | null;
  updated_at: number;
}

const TOKEN_KEY = 'argus_web_token';
let inMemoryToken: string | null = null;

/** Persist a token handed over in the URL, then drop it from the address bar.
 *
 * Pairing puts the token in a QR code, so the first load carries `?token=...`.
 * Without this the token would live only as long as that query string: a
 * reload, or launching the installed PWA from its `start_url`, would land
 * unauthenticated. Clearing the query afterwards keeps the credential out of
 * the address bar, screenshots, and the back/forward history entry. */
export function adoptTokenFromUrl(): void {
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(window.location.search);
  } catch {
    return;
  }
  const fromUrl = params.get('token');
  if (!fromUrl) return;
  inMemoryToken = fromUrl;
  try {
    localStorage.setItem(TOKEN_KEY, fromUrl);
  } catch {
    // The in-memory copy keeps this page authenticated when storage is
    // unavailable, including browsers that block storage for LAN origins.
  }
  try {
    params.delete('token');
    const query = params.toString();
    window.history.replaceState(
      null,
      '',
      `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`,
    );
  } catch {
    // Failure to scrub the address bar must not stop the app from loading.
  }
}

const token = (): string | null => {
  if (inMemoryToken) return inMemoryToken;
  try {
    return new URLSearchParams(window.location.search).get('token') ||
      localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
};

export function authHeaders(): Record<string, string> {
  const t = token();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/** Shared bearer value for non-HTTP transports such as project WebSockets. */
export function authToken(): string {
  return token() ?? '';
}

const API_META_TIMEOUT_MS = 8_000;
const API_LOCAL_READ_TIMEOUT_MS = 12_000;

export class PairingRequiredError extends Error {
  constructor() {
    super('This browser is not paired with Argus. Reopen it from Argus Desktop or use a fresh pairing link.');
    this.name = 'PairingRequiredError';
  }
}

export class LocalArgusUnavailableError extends Error {
  readonly method: string;
  readonly path: string;

  constructor(method: string, path: string, detail = 'could not reach the local Argus service') {
    super(`${method.toUpperCase()} ${path} ${detail}. Make sure Argus Desktop is running, then retry.`);
    this.name = 'LocalArgusUnavailableError';
    this.method = method.toUpperCase();
    this.path = path;
  }
}

export function isAuthenticationError(error: unknown): boolean {
  if (error instanceof PairingRequiredError) return true;
  return Boolean(
    error
    && typeof error === 'object'
    && Number((error as { status?: unknown }).status) === 401,
  );
}

export function isConnectionError(error: unknown): boolean {
  return isAuthenticationError(error) || error instanceof LocalArgusUnavailableError;
}

async function fetchArgus(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(path, init);
  } catch (error) {
    // React Query cancellation is normal lifecycle control, not a backend
    // outage. Preserve it so unmount/navigation cannot raise a false alarm.
    if (init.signal?.aborted) throw error;
    throw new LocalArgusUnavailableError(String(init.method ?? 'GET'), path);
  }
}

export async function requestWithTimeout<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number,
  consume: (response: Response) => Promise<T> | T,
): Promise<T> {
  const controller = new AbortController();
  const parentSignal = init.signal ?? undefined;
  let timedOut = false;
  let removeParentAbortListener: () => void = () => {};

  if (parentSignal) {
    const abortFromParent = () => controller.abort(parentSignal.reason);
    if (parentSignal.aborted) abortFromParent();
    else {
      parentSignal.addEventListener('abort', abortFromParent, { once: true });
      removeParentAbortListener = () => parentSignal.removeEventListener('abort', abortFromParent);
    }
  }

  let timeout: ReturnType<typeof setTimeout> | undefined;
  const operation = (async () => {
    const response = await fetchArgus(path, { ...init, signal: controller.signal });
    return await consume(response);
  })();
  const deadline = new Promise<never>((_resolve, reject) => {
    timeout = setTimeout(() => {
      timedOut = true;
      const error = new Error(`request timed out after ${timeoutMs}ms`);
      controller.abort(error);
      reject(error);
    }, timeoutMs);
  });
  try {
    return await Promise.race([operation, deadline]);
  } catch (error) {
    if (timedOut) {
      const seconds = Math.round(timeoutMs / 1_000);
      throw new LocalArgusUnavailableError(
        String(init.method ?? 'GET'),
        path,
        `timed out after ${seconds}s because the local Argus service did not respond`,
      );
    }
    throw error;
  } finally {
    if (timeout) clearTimeout(timeout);
    removeParentAbortListener();
  }
}

/** Bound connection establishment for callers that consume the body later. */
export function fetchWithTimeout(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  return requestWithTimeout(path, init, timeoutMs, (response) => response);
}

async function getJson<T>(
  path: string,
  signal?: AbortSignal,
  timeoutMs?: number,
): Promise<T> {
  const init = { headers: authHeaders(), signal };
  return requestWithTimeout(
    path,
    init,
    timeoutMs ?? API_LOCAL_READ_TIMEOUT_MS,
    async (response) => {
      await ensureResponseOk(response, 'GET', path);
      return (await response.json()) as T;
    },
  );
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

async function postMultipart<T>(
  path: string,
  body: FormData,
  signal?: AbortSignal,
): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: authHeaders(),
    body,
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

function isAbortSignal(value: unknown): value is AbortSignal {
  return Boolean(
    value
    && typeof value === 'object'
    && 'aborted' in value
    && typeof (value as AbortSignal).aborted === 'boolean',
  );
}

function messageBody(text: string, attachments?: MessageAttachmentRef[]): Record<string, unknown> {
  return attachments?.length ? { text, attachments } : { text };
}

export function compatibleApiMeta(): Promise<ApiMeta> {
  if (!apiMetaPromise) {
    const request = (async () => {
      const path = '/api/meta';
      const meta = await requestWithTimeout(
        path,
        { headers: authHeaders() },
        API_META_TIMEOUT_MS,
        async (response) => {
          if (response.status === 404) {
            throw new Error('incompatible Argus API: service does not expose /api/meta');
          }
          await ensureResponseOk(response, 'GET', path);
          return requireCompatibleApiMeta(
            await response.json(),
            (warning) => console.warn(`Argus API compatibility warning: ${warning}`),
          );
        },
      );
      if (meta.authentication?.required && !meta.authentication.authenticated) {
        throw new PairingRequiredError();
      }
      return meta;
    })();
    apiMetaPromise = request;
    void request.catch((error) => {
      // An unpaired page cannot heal by polling: it needs a new token-bearing
      // navigation. Keep that rejected handshake cached to stop a 401 storm.
      if (apiMetaPromise === request && !(error instanceof PairingRequiredError)) {
        apiMetaPromise = undefined;
      }
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

let activeSnapshotPrewarmSid: string | null = null;

export const api = {
  meta: compatibleApiMeta,
  projectIndex: async () => {
    await compatibleApiMeta();
    return getJson<ProjectIndex>('/api/projects', undefined, API_LOCAL_READ_TIMEOUT_MS);
  },
  listProjects: async () => {
    await compatibleApiMeta();
    return getJson<ProjectIndex>('/api/projects', undefined, API_LOCAL_READ_TIMEOUT_MS)
      .then((result) => result.projects);
  },
  projectCosts: async (signal?: AbortSignal) => {
    await compatibleApiMeta();
    return getJson<ProjectCostIndex>('/api/projects/costs', signal);
  },
  /** Create a session. The UI arms an optional campaign separately. */
  createDaemon: async (
    objective: string,
    name = '',
    workdir = '',
    expectedRevision?: number,
  ) => {
    const path = '/api/daemons';
    const body = {
      objective,
      name,
      workdir,
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
    const result = (await response.json()) as {
      sid: string;
      rc: number;
      daemon: Daemon;
      objective: string;
      workdir: string;
    };
    return requireDaemonCommand(result);
  },
  updateProject: (sid: string, name: string) =>
    mutationJson<{ ok: boolean; sid: string; name: string }>('PATCH', P(sid), { name }),
  deleteProject: (sid: string) =>
    mutationJson<{
      ok: boolean;
      sid: string;
      trash_path: string;
      workdir: string;
      workdir_preserved: boolean;
    }>('DELETE', P(sid)),
  snapshot: async (sid: string, signal?: AbortSignal, prewarm = false) => {
    await compatibleApiMeta();
    const value = await getJson<unknown>(
      P(sid, `/snapshot?compact=true&events_limit=1${prewarm ? '&prewarm=true' : ''}`),
      signal,
      API_LOCAL_READ_TIMEOUT_MS,
    );
    return requireSnapshotContract(value);
  },
  activeSnapshot: async (sid: string, signal?: AbortSignal): Promise<Snapshot> => {
    const prewarm = activeSnapshotPrewarmSid !== sid;
    if (prewarm) activeSnapshotPrewarmSid = sid;
    try {
      return await api.snapshot(sid, signal, prewarm);
    } catch (error) {
      if (prewarm && activeSnapshotPrewarmSid === sid) {
        activeSnapshotPrewarmSid = null;
      }
      throw error;
    }
  },
  prefetchSnapshot: (sid: string, signal?: AbortSignal) =>
    api.snapshot(sid, signal, false),
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
  sourceUpdateStatus: (signal?: AbortSignal) =>
    getJson<SourceUpdateStatus>('/api/runtime/source-update', signal),
  checkSourceUpdate: () =>
    postJson<SourceUpdateStatus>('/api/runtime/source-update/check'),
  applySourceUpdate: () =>
    postJson<SourceUpdateStatus>('/api/runtime/source-update/apply'),
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
      resolved: boolean;
      reply?: string;
      manager_decision?: string;
      item?: BacklogItem;
      daemon?: { rc?: number; error?: string; admission_required?: boolean };
    }>(
      P(sid, `/backlog/${encodeURIComponent(itemId)}/answer`),
      { text },
    ),
  resolveDecision: (
    sid: string,
    decisionId: string,
    optionId: string,
    note: string,
  ) => postJson<{
    resolved: boolean;
    stopped?: boolean;
    reply?: string;
    daemon?: { rc?: number; error?: string; admission_required?: boolean };
  }>(
    P(sid, `/decisions/${encodeURIComponent(decisionId)}/resolve`),
    { option_id: optionId, note },
  ),
  uploadAttachments: async (
    sid: string,
    files: File[],
    signal?: AbortSignal,
  ) => {
    await compatibleApiMeta();
    const form = new FormData();
    files.forEach((file) => form.append('files', file, file.name));
    return postMultipart<AttachmentUploadResponse>(P(sid, '/attachments'), form, signal);
  },
  /** The Manager front-door: NL message → chat reply or an enqueued mission. */
  message: (
    sid: string,
    text: string,
    signalOrOptions?: AbortSignal | {
      signal?: AbortSignal;
      attachments?: MessageAttachmentRef[];
    },
  ) => {
    const signal = isAbortSignal(signalOrOptions) ? signalOrOptions : signalOrOptions?.signal;
    const attachments = isAbortSignal(signalOrOptions) ? undefined : signalOrOptions?.attachments;
    return postJson<{ kind: 'chat' | 'task' | 'pending_question' | 'pending_question_choice' | 'error'; reply: string | null; resolved?: boolean; item?: BacklogItem | null; daemon_alive?: boolean }>(
      P(sid, '/message'),
      messageBody(text, attachments),
      signal,
    );
  },
  /**
   * Streaming Manager front-door (SSE): ``onPhase`` per real step, ``onDelta``
   * per reply block as it's produced, ``onDone`` with the final classification,
   * ``onError`` on failure. Un-freezes the UI — Argus visibly thinks and the
   * answer types in. Callers must not automatically replay a failed POST.
   */
  messageStream: async (
    sid: string,
    text: string,
    handlers: {
      onPhase?: (
        label: string,
        role: string,
        meta: { heartbeat: boolean; quietS: number; kind: string; detail: string },
      ) => void;
      onDelta?: (block: string, messageId: string, fragmentMode: string) => void;
      onDone?: (result: StreamDone) => void;
      onError?: (err: Error) => void;
    },
    signalOrOptions?: AbortSignal | {
      signal?: AbortSignal;
      attachments?: MessageAttachmentRef[];
    },
  ): Promise<void> => {
    const signal = isAbortSignal(signalOrOptions) ? signalOrOptions : signalOrOptions?.signal;
    const attachments = isAbortSignal(signalOrOptions) ? undefined : signalOrOptions?.attachments;
    const res = await fetch(P(sid, '/message/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(messageBody(text, attachments)),
      signal,
    });
    await ensureResponseOk(res, 'POST', P(sid, '/message/stream'));
    if (!res.body) throw new Error('Manager stream returned no response body');
    let sawTerminal = false;
    const dispatch = (f: SSEFrame) => {
      if (signal?.aborted) return;
      if (f.type === 'phase') {
        const quietS = Number(f.quiet_s ?? 0);
        handlers.onPhase?.(
          String(f.label ?? ''),
          String(f.role ?? 'manager'),
          {
            heartbeat: f.heartbeat === true,
            quietS: Number.isFinite(quietS) ? quietS : 0,
            kind: String(f.kind ?? ''),
            detail: String(f.detail ?? ''),
          },
        );
      }
      else if (f.type === 'delta') {
        handlers.onDelta?.(
          String(f.text ?? ''),
          String(f.message_id ?? ''),
          String(f.fragment_mode ?? 'auto'),
        );
      }
      else if (f.type === 'done') {
        sawTerminal = true;
        handlers.onDone?.((f.result ?? {}) as StreamDone);
      }
      else if (f.type === 'error') {
        sawTerminal = true;
        handlers.onError?.(new Error(String(f.error ?? 'stream error')));
      }
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
    if (!signal?.aborted) {
      parseSSEFrames(buf + '\n\n').frames.forEach(dispatch);
      if (!sawTerminal) throw new Error('Manager stream ended before a terminal event');
    }
  },
  nudge: (sid: string, text: string) => postJson(P(sid, '/nudge'), { text }),
  note: (sid: string, text: string) => postJson(P(sid, '/note'), { text }),
  previewPlan: (sid: string, text: string) =>
    postJson<PlanPreview>(P(sid, '/plan'), { text }),
  /**
   * Ask the Manager to restate a short draft as an executable brief. Preview
   * only — nothing is queued; the operator edits/sends the result themselves.
   */
  rewritePrompt: (sid: string, text: string) =>
    postJson<PromptRewrite>(P(sid, '/prompt/rewrite'), { text }),
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
export type StreamCloseInfo = {
  code: number;
  reason: string;
  retryable: boolean;
};

const NON_RETRYABLE_STREAM_CLOSE_CODES = new Set([4401, 4404]);

export function openStream(
  sid: string,
  onEvent: (ev: EventMsg) => void,
  opts: {
    replay?: number;
    onOpen?: () => void;
    onClose?: (info: StreamCloseInfo) => void;
  } = {},
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
    ws.onclose = (event) => {
      const retryable = !NON_RETRYABLE_STREAM_CLOSE_CODES.has(event.code);
      opts.onClose?.({ code: event.code, reason: event.reason, retryable });
      if (!closed && retryable) retry = setTimeout(connect, 1000); // reconnect with backoff
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
