import type { ConversationPage, UploadedAttachment } from '@argus/contracts';
/**
 * Browser client for the argus webapi — same surface as the terminal client
 * (frontend/tui/src/api.ts) but using the browser fetch, with server-sent events for streams.
 * URLs are relative so Vite proxies /api in dev and the API serves it in prod.
 */

import type {
  ArtifactInfo,
  BacklogItem,
  Daemon,
  EventMsg,
  ProjectRow,
  Snapshot,
} from '../../core/src/types';
import { ApiError, ensureResponseOk } from '../../core/src/http';
import {
  requireCompatibleApiMeta,
  requireSnapshotContract,
  type ApiMeta,
} from '../../core/src/protocol';
import type { ResourceStatus } from '../../core/src/resourceStatus.generated';
import {
  attachmentMime, type AttachmentLimits,
} from './lib/attachments';

export type {
  ArtifactInfo,
  BacklogItem,
  Daemon,
  EventMsg,
  ProjectRow,
  Role,
  Snapshot,
  UsageSummary,
} from '../../core/src/types';
export type { ResourceStatus } from '../../core/src/resourceStatus.generated';


export type SkillScope = 'global' | 'vertical' | 'project';
export interface SkillLibraryItem {
  library: string;
  scope: SkillScope;
  vertical: string;
  source: 'bundled' | 'shared' | 'project' | 'native';
  path: string;
  name: string;
  description: string;
  role: string;
  is_default: boolean;
  updated_at: number | null;
}
export interface SkillCatalog {
  scopes: SkillScope[];
  items: SkillLibraryItem[];
  verticals: string[];
  active_vertical: string;
  errors: string[];
}
export interface SkillDocument {
  name: string;
  description: string;
  content: string;
  markdown: string;
  path: string;
  source: SkillLibraryItem['source'];
  scope: SkillScope;
  vertical: string;
  role: string;
}

export type LearningChannel = 'knowledge' | 'skills' | 'preferences';
export interface LearningJob {
  id: string;
  /** skipped = not attempted: nothing to learn, or model calls were paused by host policy. */
  status: 'running' | 'completed' | 'unchanged' | 'skipped' | 'failed';
  created: number;
  updated: number;
  attempts: number;
  outcome: {
    counts?: Record<LearningChannel, number>;
    items?: { channel: LearningChannel; title: string; scope: string; path: string }[];
    /** saved | no_new_learning | not_needed | paused | failed */
    reason?: string;
    /** Why calls were paused: cost_unreconciled | budget_exhausted | provider_cooldown | operator_pause. */
    detail?: string;
  };
}
export interface LearningState { jobs: LearningJob[]; pending: number; revision: number }
/** One line of the host's knowledge journal: something learned, recalled into a prompt, promoted to a shared level, corrected or retired. */
export type KnowledgeEventKind = 'learned' | 'recalled' | 'promoted' | 'corrected' | 'retired';
export interface KnowledgeEvent {
  ts: number;
  kind: KnowledgeEventKind;
  scope: SkillScope;
  vertical: string;
  /** Page path relative to its library root, e.g. "pages/lessons/20260917-torch-search.md" or "principles.md". */
  path: string;
  title: string;
  source_project: string;
  mission_id: string;
  role: string;
  page_kind: string;
  note: string;
}
/** The knowledge journal, newest first. */
export interface KnowledgeFeed {
  events: KnowledgeEvent[];
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
  /** What config.json holds for this knob, even when the environment shadows it. */
  saved?: string;
  default: string;
  doc: string;
}
export interface ConfigSnapshot {
  schema_version: number;
  generated_at_utc: string;
  roles: ConfigRole[];
  operator_knobs: ConfigKnob[];
  how_to_change: string[];
  /** Every backend there is, by its name for the page. */
  backends?: Record<string, string>;
}
/** One model the backend offers: whether it is the backend's default, and its price. */
export interface ModelChoice {
  model: string;
  default: boolean;
  /** USD per million tokens; null when the price table does not list the model. */
  price: { input: number; output: number } | null;
}
export interface ModelChoices {
  backend: string;
  options: ModelChoice[];
}
export interface ModelProbeResult {
  model: string;
  ok: boolean;
  error: string;
  duration_ms: number;
}
export interface ProjectIndex {
  projects: ProjectRow[];
  local_cwd: string;
}
/** What a server without POLL_CAPABILITY pushes: the project list as the page connects, then each
 * project's state as it changes; a deleted project's is null. */
/** What a page's stream says of the projects: the list as it connects, with the page the server
 * sends (`build`); then a project's row as it changes, with its snapshot to the page showing it. */
export type ProjectState =
  | ({ type: 'projects'; build?: string } & ProjectIndex)
  | { type: 'project'; id: string; row: ProjectRow | null; snapshot?: Snapshot | null };
/** A Manager-authored restatement of an operator draft (see the rewrite button). */
export interface PromptRewrite {
  original: string;
  rewritten: string;
  changes: string[];
  questions: string[];
  error: string;
  error_code?: string;
}
export type { UploadedAttachment } from '@argus/contracts';
export type MessageAttachmentRef = UploadedAttachment;
/** Operator-owned message category; Task skips only the category classifier. */
export type MessageRouteOverride = 'auto' | 'chat' | 'task';
export interface AttachmentUploadResponse {
  attachments: UploadedAttachment[];
  limits: {
    max_count: number;
    max_bytes_per_file: number;
    max_total_bytes: number;
  };
}
export interface AttachmentUploadProgress {
  name: string;
  index: number;
  count: number;
  loaded: number;
  total: number;
  phase: 'uploading' | 'validating' | 'ready' | 'retrying';
}
export interface AttachmentUploadOptions {
  ready?: UploadedAttachment[];
  completed?: Map<File, UploadedAttachment>;
  onUploaded?: (file: File, attachment: UploadedAttachment) => void;
  onProgress?: (progress: AttachmentUploadProgress) => void;
  onCleanupError?: (error: unknown) => void;
}
interface AttachmentUploadState {
  attachment_id: string;
  offset: number;
  size_bytes: number;
  complete: boolean;
  attachment?: UploadedAttachment;
}
const pendingUploads = new WeakMap<File, Map<string, string>>();
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

/** Open the page again with a pasted token, which `adoptTokenFromUrl` keeps, if the server takes
 * it; whether it did. */
export async function pairWithToken(credential: string): Promise<boolean> {
  const response = await fetch('/api/meta', { headers: { Authorization: `Bearer ${credential}` } });
  await ensureResponseOk(response, 'GET', '/api/meta');
  if (!(await response.json() as ApiMeta).authentication?.authenticated) return false;
  const page = new URL(window.location.href);
  page.searchParams.set('token', credential);
  window.location.replace(page);
  return true;
}

/** Forget this browser's token and open the page again, unpaired: the server holds no session to end. */
export function signOut(): void {
  inMemoryToken = null;
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing was kept there.
  }
  window.location.reload();
}

export const holdsToken = (): boolean => Boolean(token());

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

const API_META_TIMEOUT_MS = 8_000;
const API_LOCAL_READ_TIMEOUT_MS = 12_000;

export class PairingRequiredError extends Error {
  constructor() {
    super('This browser is not paired with Argus. Open a valid pairing link or enter a pairing token.');
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
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    // React Query cancellation is normal lifecycle control, not a backend
    // outage. Preserve it so unmount/navigation cannot raise a false alarm.
    if (init.signal?.aborted) throw error;
    throw new LocalArgusUnavailableError(String(init.method ?? 'GET'), path);
  }
  return response;
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

async function postResponse(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  const r = await fetchArgus(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  await ensureResponseOk(r, 'POST', path);
  return r;
}

async function postJson<T = Record<string, unknown>>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return (await (await postResponse(path, body, signal)).json()) as T;
}

async function uploadRequest<T>(
  method: string,
  path: string,
  body?: Blob | Record<string, unknown>,
  signal?: AbortSignal,
): Promise<T> {
  return requestWithTimeout(path, {
    method,
    headers: {
      ...authHeaders(),
      ...(body === undefined ? {} : { 'Content-Type': body instanceof Blob ? 'application/octet-stream' : 'application/json' }),
    },
    body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body),
    signal,
  }, method === 'DELETE' ? 5_000 : 120_000, async response => {
    await ensureResponseOk(response, method, path);
    return await response.json() as T;
  });
}

function uploadSmallAttachments(
  path: string, files: File[], signal?: AbortSignal,
  onProgress?: AttachmentUploadOptions['onProgress'],
): Promise<AttachmentUploadResponse> {
  signal?.throwIfAborted();
  const xhr = new XMLHttpRequest();
  const abort = () => xhr.abort();
  const total = files.reduce((sum, file) => sum + file.size, 0);
  const progress = (sent: number, phase: AttachmentUploadProgress['phase']) => {
    let index = 0;
    while (index < files.length - 1 && sent >= files[index].size) sent -= files[index++].size;
    const file = files[index];
    onProgress?.({ name: file.name, index: index + 1, count: files.length,
      loaded: Math.min(sent, file.size), total: file.size, phase });
  };
  return new Promise<AttachmentUploadResponse>((resolve, reject) => {
    xhr.open('POST', path);
    xhr.timeout = 120_000;
    for (const [name, value] of Object.entries(authHeaders())) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = event => {
      if (event.lengthComputable && event.total > 0) {
        progress(Math.floor(total * event.loaded / event.total), 'uploading');
      }
    };
    xhr.upload.onload = () => progress(total, 'validating');
    xhr.onload = async () => {
      try {
        await ensureResponseOk({
          ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status,
          statusText: xhr.statusText, text: async () => xhr.responseText,
        }, 'POST', path);
        resolve(JSON.parse(xhr.responseText) as AttachmentUploadResponse);
      } catch (error) { reject(error); }
    };
    xhr.onerror = () => reject(new LocalArgusUnavailableError('POST', path,
      'lost the upload response; completion was not confirmed (no automatic retry)'));
    xhr.ontimeout = () => reject(new LocalArgusUnavailableError('POST', path,
      'timed out after 120s; completion was not confirmed (no automatic retry)'));
    xhr.onabort = () => reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'));
    signal?.addEventListener('abort', abort, { once: true });
    const body = new FormData();
    files.forEach(file => body.append('files', file.slice(0, file.size, attachmentMime(file)), file.name));
    progress(0, 'uploading');
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    xhr.send(body);
  }).finally(() => signal?.removeEventListener('abort', abort));
}

export function requireDaemonCommand<T>(result: T): T {
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
  const r = await fetchArgus(path, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  await ensureResponseOk(r, method, path);
  return (await r.json()) as T;
}

async function getBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const r = await fetchArgus(path, { headers: authHeaders(), signal });
  await ensureResponseOk(r, 'GET', path);
  return r.blob();
}

const P = (sid: string, path = '') => `/api/projects/${encodeURIComponent(sid)}${path}`;

const artifactParams = (path: string, delivery?: string) => new URLSearchParams({ path, ...(delivery ? { delivery } : {}) });

/** URL of the served, self-sandboxing preview page for an HTML result. A
 * sandboxed iframe loads it by URL so the delivered site keeps its own styles
 * and scripts; it cannot send an auth header, so it presents the cookie that
 * `api.artifactPreview` received (a hosted portal adds the header upstream). */
export const previewPageUrl = (sid: string, path: string, delivery?: string): string =>
  P(sid, `/artifact/preview/page?${artifactParams(path, delivery)}`);
export const newRequestId = (): string => globalThis.crypto?.randomUUID?.()
  ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
const commandId = newRequestId;
let apiMetaPromise: Promise<ApiMeta> | undefined;

function isAbortSignal(value: unknown): value is AbortSignal {
  return Boolean(
    value
    && typeof value === 'object'
    && 'aborted' in value
    && typeof (value as AbortSignal).aborted === 'boolean',
  );
}

function messageBody(
  text: string,
  attachments?: MessageAttachmentRef[],
  routeOverride?: MessageRouteOverride,
  requestId?: string,
): Record<string, unknown> {
  const body: Record<string, unknown> = { text };
  if (attachments?.length) body.attachments = attachments;
  if (routeOverride && routeOverride !== 'auto') body.route_override = routeOverride;
  if (requestId) body.request_id = requestId;
  return body;
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
          const payload = await response.json();
          return requireCompatibleApiMeta(payload);
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

/** One decoded SSE frame from a streaming Argus endpoint. */
export interface SSEFrame {
  type: string; // heartbeat | phase | delta | done | error
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
  let separator: RegExpExecArray | null;
  while ((separator = /\r?\n\r?\n/.exec(buf))) {
    const raw = buf.slice(0, separator.index);
    buf = buf.slice(separator.index + separator[0].length);
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

/** Share Manager/map framing and wait for a terminal result, never a heartbeat. */
async function readSSE(
  response: Response,
  label: string,
  onFrame?: (frame: SSEFrame) => void,
  signal?: AbortSignal,
): Promise<SSEFrame> {
  if (!response.body) throw new Error(`${label} returned no response body`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let terminal: SSEFrame | undefined;
  let reachedEOF = false;
  try {
    for (;;) {
      signal?.throwIfAborted();
      const { done, value } = await reader.read();
      reachedEOF = done;
      signal?.throwIfAborted();
      buffer += done ? decoder.decode() + '\n\n' : decoder.decode(value, { stream: true });
      const parsed = parseSSEFrames(buffer);
      buffer = parsed.rest;
      for (const frame of parsed.frames) {
        onFrame?.(frame);
        if (frame.type === 'done' || frame.type === 'error') terminal = frame;
      }
      if (done) {
        if (!terminal) throw new Error(`${label} ended before a terminal event`);
        return terminal;
      }
    }
  } finally {
    // Drain normal responses so the portal records complete collection, rather
    // than interpreting a cancelled body after `done` as a client disconnect.
    if (!reachedEOF) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export interface ProjectPreference { pinned: boolean; archived: boolean; followup: boolean }
export interface Edition { version: number; content: string; note: string; created: number }
export interface SearchResult {
  sid: string; title: string; kind: 'project' | 'conversation' | 'artifact' | 'edition';
  snippet: string; text: string; path?: string; version?: number; role?: string;
  ts?: number; line?: number; message_id?: string; task_id?: string; truncated?: boolean;
}

export const api = {
  organization: () => getJson<{ value: Record<string, ProjectPreference>; revision: number }>('/api/browser-state/organization'),
  organize: (sid: string, field: keyof ProjectPreference, value: boolean) =>
    postJson<{ value: Record<string, ProjectPreference>; revision: number }>('/api/browser-state/organization', { sid, field, value }),
  searchHistory: (query: string, signal?: AbortSignal) =>
    getJson<{ results: SearchResult[]; partial: boolean; errors: string[] }>(`/api/search?q=${encodeURIComponent(query)}`, signal, 30_000),
  editions: (sid: string, source: string) =>
    getJson<{ original: string; versions: Edition[] }>(P(sid, `/editions/artifact?source=${encodeURIComponent(source)}`)),
  saveEdition: (sid: string, value: { source: string; content: string; note?: string; base_version: number }) =>
    postJson<{ version: number; content: string }>(P(sid, '/editions/artifact'), value),
  /** What `content` changes in the text result `source` as it is now, as a unified diff. */
  editionDiff: (sid: string, source: string, content: string) =>
    postJson<{ diff: string; truncated: boolean }>(P(sid, '/editions/artifact/diff'), { source, content }),
  liveMap: (sid: string, signal?: AbortSignal, after?: string) => {
    const params = new URLSearchParams();
    if (after) params.set('after', after);
    return getJson<import('./map/model').Dataset>(P(sid, '/map') + (params.size ? `?${params}` : ''), signal);
  },
  /** One task's recorded trajectory: what each role thought, said, ran and changed. */
  mapTrace: (sid: string, task: string, signal?: AbortSignal) =>
    getJson<import('./components/TaskTrace').TaskTraceData>(P(sid, '/map-trace') + `?task=${encodeURIComponent(task)}`, signal),
  meta: compatibleApiMeta,
  projectIndex: async () => {
    await compatibleApiMeta();
    return getJson<ProjectIndex>('/api/projects', undefined, API_LOCAL_READ_TIMEOUT_MS);
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
    const response = await fetchArgus(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
      cache: 'no-store',
    });
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
  snapshot: async (sid: string, signal?: AbortSignal) => {
    await compatibleApiMeta();
    const value = await getJson<unknown>(
      P(sid, '/snapshot'),
      signal,
      API_LOCAL_READ_TIMEOUT_MS,
    );
    return requireSnapshotContract(value);
  },
  config: (sid: string, signal?: AbortSignal) =>
    getJson<ConfigSnapshot>(P(sid, '/config'), signal),
  modelChoices: (sid: string, signal?: AbortSignal) =>
    getJson<ModelChoices>(P(sid, '/model-choices'), signal),
  probeModel: (sid: string, model: string) =>
    postJson<ModelProbeResult>(P(sid, '/model-choices/probe'), { model }),
  conversation: (sid: string, before?: number, signal?: AbortSignal) =>
    getJson<ConversationPage>(P(sid, `/conversation?limit=30${before === undefined ? '' : `&before=${before}`}`), signal),
  backlogItem: (sid: string, id: string, signal?: AbortSignal, letter?: string) =>
    getJson<{ item: BacklogItem }>(
      P(sid, `/backlog/${encodeURIComponent(id)}${letter ? `?letter=${encodeURIComponent(letter)}` : ''}`),
      signal,
    ).then((r) => r.item),
  artifacts: (sid: string, signal?: AbortSignal) =>
    getJson<{ artifacts: ArtifactInfo[] }>(P(sid, '/artifacts'), signal)
      .then((r) => r.artifacts),
  artifact: (sid: string, path: string, signal?: AbortSignal, delivery?: string) => {
    const q = artifactParams(path, delivery);
    return getJson<ArtifactInfo>(P(sid, `/artifact?${q}`), signal);
  },
  artifactPreview: (sid: string, path: string, signal?: AbortSignal, delivery?: string) =>
    getJson<{ html: string; warnings: string[]; file_count: number; served_page?: boolean }>(
      P(sid, `/artifact/preview?${artifactParams(path, delivery)}`), signal),
  /** One zip of listed results (each path passes the artifact allowlist). */
  artifactsBundle: async (sid: string, paths: string[], name = '', summary = '', signal?: AbortSignal, delivery?: string) =>
    (await postResponse(P(sid, '/artifacts/bundle'), { paths, name, summary, ...(delivery ? { delivery } : {}) }, signal)).blob(),
  artifactBundle: (sid: string, path: string, signal?: AbortSignal, delivery?: string) =>
    getBlob(P(sid, `/artifact/bundle?${artifactParams(path, delivery)}`), signal),
  artifactBlob: (
    sid: string,
    path: string,
    download = false,
    signal?: AbortSignal,
    delivery?: string,
  ) => {
    const q = artifactParams(path, delivery);
    if (download) q.set('download', 'true');
    return getBlob(P(sid, `/artifact/raw?${q}`), signal);
  },
  artifactRawUrl: (sid: string, path: string, delivery?: string) =>
    P(sid, `/artifact/raw?${artifactParams(path, delivery)}`),
  /** A mesh, glTF buffer or texture a registered model references, by the reference as written. */
  artifactModelAsset: (sid: string, path: string, ref: string, signal?: AbortSignal, delivery?: string) =>
    getBlob(P(sid, `/artifact/model-asset?${new URLSearchParams({ path, ref, ...(delivery ? { delivery } : {}) })}`), signal),
  deliveryDiff: (sid: string, change: { path: string; before: string; after: string }, signal?: AbortSignal) =>
    getJson<{ path: string; available: boolean; diff: string; truncated: boolean }>(
      P(sid, `/delivery-diff?${new URLSearchParams(change)}`), signal),
  resources: (signal?: AbortSignal) =>
    getJson<ResourceStatus>('/api/system/resources', signal),
  trash: (query = '', offset = 0, signal?: AbortSignal) =>
    getJson<{ entries: { trash_id: string; sid: string; label: string; launch_cwd: string; trashed_at: number }[]; total: number; offset: number; limit: number }>(
      `/api/trash?${new URLSearchParams({ query, offset: String(offset) })}`, signal),
  restoreTrash: (trashId: string) =>
    postJson<{ ok: boolean; sid: string }>(`/api/trash/${encodeURIComponent(trashId)}/restore`),

  resolveDecision: (
    sid: string,
    decisionId: string,
    optionId: string,
    note: string,
    attachments: UploadedAttachment[] = [],
  ) => postJson<{
    resolved: boolean;
    stopped?: boolean;
    reply?: string;
    daemon?: { rc?: number; error?: string };
  }>(
    P(sid, `/decisions/${encodeURIComponent(decisionId)}/resolve`),
    { option_id: optionId, note, ...(attachments.length ? { attachments } : {}) },
  ),
  attachmentLimits: (sid: string, signal?: AbortSignal) => getJson<AttachmentLimits>(P(sid, '/attachments/limits'), signal),
  uploadAttachments: async (
    sid: string,
    files: File[],
    signal?: AbortSignal,
    options: AttachmentUploadOptions = {},
  ): Promise<AttachmentUploadResponse> => {
    await compatibleApiMeta();
    signal?.throwIfAborted();
    const attachments = [...(options.ready ?? [])];
    const limits = await api.attachmentLimits(sid, signal);
    if (![limits.max_count, limits.max_bytes_per_file, limits.max_total_bytes, limits.chunk_bytes]
      .every(value => Number.isSafeInteger(value) && value > 0)) throw new Error('Invalid attachment upload limits from server');
    if (files.length + attachments.length > limits.max_count
      || files.some(file => file.size > limits.max_bytes_per_file)
      || files.reduce((sum, file) => sum + file.size, attachments.reduce((sum, file) => sum + file.size_bytes, 0)) > limits.max_total_bytes) {
      throw new Error('Attachments exceed the server file/count/total limits');
    }
    const fresh = files.filter(file => !options.completed?.has(file));
    if (fresh.length === 0) {
      for (const file of files) {
        const attachment = options.completed?.get(file);
        if (!attachment) throw new Error('Missing completed attachment receipt');
        attachments.push(attachment);
      }
      return { attachments, limits };
    }
    // Small files share one request; unfinished uploads keep their existing identity.
    if (fresh.length > 0
      && fresh.every(file => file.size <= limits.chunk_bytes
        && !pendingUploads.get(file)?.has(sid))
      && fresh.reduce((sum, file) => sum + file.size, 0) <= limits.chunk_bytes) {
      const result = await uploadSmallAttachments(P(sid, '/attachments'), fresh, signal, progress =>
        options.onProgress?.({ ...progress, index: files.indexOf(fresh[progress.index - 1]) + 1, count: files.length }));
      if (!Array.isArray(result.attachments) || result.attachments.length !== fresh.length
        || result.attachments.some((attachment, index) =>
          !attachment.attachment_id || attachment.size_bytes !== fresh[index].size)) {
        throw new Error('Server did not return a complete attachment batch');
      }
      const received = new Map(fresh.map((file, index) => [file, result.attachments[index]]));
      for (const [index, file] of files.entries()) {
        const attachment = options.completed?.get(file) ?? received.get(file);
        if (!attachment) throw new Error('Server did not return a completed attachment');
        attachments.push(attachment);
        if (received.has(file)) options.onUploaded?.(file, attachment);
        options.onProgress?.({ name: file.name, index: index + 1, count: files.length,
          loaded: file.size, total: file.size, phase: 'ready' });
      }
      signal?.throwIfAborted();
      return { attachments, limits: result.limits };
    }
    for (const [index, file] of files.entries()) {
      signal?.throwIfAborted();
      const progress = (loaded: number, phase: AttachmentUploadProgress['phase']) =>
        options.onProgress?.({ name: file.name, index: index + 1, count: files.length, loaded, total: file.size, phase });
      const cached = options.completed?.get(file);
      if (cached) {
        attachments.push(cached);
        progress(file.size, 'ready');
        continue;
      }
      let fileUploads = pendingUploads.get(file);
      if (!fileUploads) { fileUploads = new Map(); pendingUploads.set(file, fileUploads); }
      let id = fileUploads.get(sid);
      let path = id ? P(sid, `/attachments/uploads/${encodeURIComponent(id)}`) : '';
      try {
        let state = id
          ? await uploadRequest<AttachmentUploadState>('GET', path, undefined, signal)
          : await uploadRequest<AttachmentUploadState>('POST', P(sid, '/attachments/uploads'),
            { name: file.name, mime: attachmentMime(file), size_bytes: file.size }, signal);
        id = state.attachment_id;
        fileUploads.set(sid, id);
        path = P(sid, `/attachments/uploads/${encodeURIComponent(id)}`);
        let failures = 0;
        while (!state.complete) {
          signal?.throwIfAborted();
          if (!Number.isSafeInteger(state.offset) || state.offset < 0 || state.offset > file.size || state.size_bytes !== file.size) {
            throw new Error('Invalid upload offset or file identity returned by server');
          }
          const completing = state.offset === file.size;
          progress(state.offset, completing ? 'validating' : 'uploading');
          try {
            state = completing
              ? await uploadRequest<AttachmentUploadState>('POST', `${path}/complete`, {}, signal)
              : await uploadRequest<AttachmentUploadState>('PUT', `${path}?offset=${state.offset}`,
                file.slice(state.offset, Math.min(file.size, state.offset + limits.chunk_bytes)), signal);
            failures = 0;
          } catch (error) {
            if (signal?.aborted || (error instanceof ApiError && ![409, 429, 500, 502, 503, 504].includes(error.status)) || ++failures > 2) throw error;
            progress(state.offset, 'retrying');
            await new Promise<void>((resolve, reject) => {
              const abort = () => { clearTimeout(timer); reject(signal?.reason ?? new DOMException('Aborted', 'AbortError')); };
              const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, 500 * failures);
              signal?.addEventListener('abort', abort, { once: true });
              if (signal?.aborted) abort();
            });
            // A lost acknowledgement may already have committed the bytes.
            // Always ask the server before writing again; never replay a task.
            state = await uploadRequest<AttachmentUploadState>('GET', path, undefined, signal);
          }
        }
        if (!state.attachment || state.attachment.attachment_id !== id || state.attachment.size_bytes !== file.size) {
          throw new Error('Server did not return a completed attachment');
        }
        attachments.push(state.attachment);
        options.onUploaded?.(file, state.attachment);
        fileUploads.delete(sid);
        progress(file.size, 'ready');
      } catch (error) {
        if (id && (signal?.aborted || (error instanceof ApiError && [400, 413, 415, 422].includes(error.status)))) {
          try {
            await uploadRequest('DELETE', path);
            fileUploads.delete(sid);
          } catch (cleanupError) {
            options.onCleanupError?.(cleanupError);
            if (!options.onCleanupError) throw new Error(`Upload failed; temporary cleanup also failed: ${String(cleanupError)}`, { cause: error });
          } finally {
            if (signal?.aborted) fileUploads.delete(sid);
          }
        }
        if (error instanceof ApiError && error.status === 404) fileUploads.delete(sid);
        throw error;
      }
    }
    return { attachments, limits };
  },
  /** Delete a file the operator removed from the draft before sending it. */
  discardAttachment: (sid: string, attachmentId: string) =>
    uploadRequest<{ discarded: boolean; attachment_id: string }>('DELETE', P(sid, `/attachments/${encodeURIComponent(attachmentId)}`)),
  /**
   * Streaming Manager front-door (SSE): ``onAccepted`` when the request starts, ``onDelta``
   * with the reply's row in the record, ``onDone`` with the final classification,
   * ``onError`` on failure. Callers must not automatically replay a failed POST.
   */
  messageStream: async (
    sid: string,
    text: string,
    handlers: {
      onAccepted?: () => void;
      /** The reply, as the conversation has it once the record is read. */
      onDelta?: (reply: EventMsg) => void;
      onDone?: (result: StreamDone) => void;
      onError?: (err: Error) => void;
    },
    signalOrOptions?: AbortSignal | {
      signal?: AbortSignal;
      attachments?: MessageAttachmentRef[];
      routeOverride?: MessageRouteOverride;
      requestId?: string;
    },
  ): Promise<void> => {
    const signal = isAbortSignal(signalOrOptions) ? signalOrOptions : signalOrOptions?.signal;
    const attachments = isAbortSignal(signalOrOptions) ? undefined : signalOrOptions?.attachments;
    const routeOverride = isAbortSignal(signalOrOptions) ? undefined : signalOrOptions?.routeOverride;
    const requestId = isAbortSignal(signalOrOptions) ? undefined : signalOrOptions?.requestId;
    const res = await postResponse(P(sid, '/message/stream'), messageBody(text, attachments, routeOverride, requestId), signal);
    const dispatch = (f: SSEFrame) => {
      if (signal?.aborted) return;
      if (f.type === 'phase') handlers.onAccepted?.();
      else if (f.type === 'delta') handlers.onDelta?.(f.event as EventMsg);
      else if (f.type === 'done') {
        handlers.onDone?.((f.result ?? {}) as StreamDone);
      }
      else if (f.type === 'error') {
        handlers.onError?.(new Error(String(f.error ?? 'stream error')));
      }
    };
    await readSSE(res, 'Manager stream', dispatch, signal);
  },
  /** A message typed while a Manager reply runs: shown now, answered as the next turn. */
  queueFollowup: (sid: string, text: string, routeOverride: MessageRouteOverride = 'auto') =>
    postJson<{ ok: boolean; queued: boolean; message_id: string }>(P(sid, '/message/followup'), { text, route_override: routeOverride }),
  /**
   * Ask the Manager to restate a short draft as an executable brief. Preview
   * only — nothing is queued; the operator edits/sends the result themselves.
   */
  rewritePrompt: (sid: string, text: string) =>
    postJson<PromptRewrite>(P(sid, '/prompt/rewrite'), { text }),
  setConfig: (sid: string, name: string, value: string) =>
    postJson<Record<string, unknown>>(P(sid, '/config/set'), { name, value }),
  resetManager: (sid: string) =>
    postJson<{ ok: boolean }>(P(sid, '/reset')),
  skillLibrary: (sid: string | null, signal?: AbortSignal) =>
    getJson<SkillCatalog>(`/api/skill-library${sid ? `?sid=${encodeURIComponent(sid)}` : ''}`, signal),
  skillDocument: (sid: string | null, library: string, path: string, signal?: AbortSignal) =>
    getJson<SkillDocument>(`/api/skill-library/document?${new URLSearchParams({ library, path, ...(sid ? { sid } : {}) })}`, signal),
  knowledgeFeed: (limit = 50, signal?: AbortSignal) =>
    getJson<KnowledgeFeed>(`/api/knowledge/feed?${new URLSearchParams({ limit: String(limit) })}`, signal),
  learningStatus: (sid: string, signal?: AbortSignal) => getJson<LearningState>(P(sid, '/learning'), signal),
  setWorkdir: (sid: string, workdir: string) =>
    postJson<{ ok: boolean; workdir: string; unchanged?: boolean }>(
      P(sid, '/workdir'),
      { workdir },
    ),
  startDaemon: (sid: string, expectedRevision?: number) => postJson(P(sid, '/daemon/start'), {
    command_id: commandId(),
    expected_revision: expectedRevision,
  }).then(requireDaemonCommand),
  /** Stop the work at once: the calls under way are interrupted. */
  stopDaemon: (sid: string, expectedRevision?: number) => postJson(P(sid, '/daemon/stop'), {
    command_id: commandId(),
    expected_revision: expectedRevision,
  }).then(requireDaemonCommand),
};

/** How the live event stream closed: `code` is the HTTP status that refused it, 0 for a lost
 * connection. An unknown project or a refused token does not reconnect. */
export type StreamCloseInfo = {
  code: number;
  reason: string;
  retryable: boolean;
};

const NON_RETRYABLE_STREAM_STATUSES = new Set([401, 404]);

type StreamOptions = { onOpen?: () => void; onClose?: (info: StreamCloseInfo) => void };

/** A page's one stream while it shows a project: its rows, in the projection of a fresh conversation
 * page, and the projects' states (ProjectState). A browser gives a page few connections to a server. */
export const openConversation = (
  sid: string, after: number, onFrame: (frame: EventMsg) => void, opts: StreamOptions = {},
): (() => void) => follow(P(sid, `/stream?view=rows&after=${after}`), 'Conversation stream', onFrame, opts);

/** A page's one stream while it shows no project: the projects' states. Returns a close() fn. */
export const openProjectStates = (onState: (state: ProjectState) => void, opts: StreamOptions = {}): (() => void) =>
  follow('/api/projects/stream', 'Project stream', (frame) => onState(frame as ProjectState), opts);

/** Follow server-sent events, connecting again a second after a lost connection and twice as long
 * after each further failure, up to ten seconds; a record's stream is told the last event the page
 * has (Last-Event-ID) and goes on after it. */
function follow(path: string, label: string, onFrame: (frame: EventMsg) => void, opts: StreamOptions): () => void {
  const controller = new AbortController();
  let retry: ReturnType<typeof setTimeout> | undefined;
  let last: unknown;
  let wait = 1000;
  const connect = async () => {
    let info: StreamCloseInfo;
    try {
      const headers = last === undefined ? authHeaders() : { ...authHeaders(), 'Last-Event-ID': String(last) };
      const response = await fetch(path, { headers, signal: controller.signal });
      if (!response.ok) {
        info = { code: response.status, reason: response.statusText, retryable: !NON_RETRYABLE_STREAM_STATUSES.has(response.status) };
      } else {
        opts.onOpen?.();
        wait = 1000;
        await readSSE(response, label, (frame) => {
          if (frame.event_id !== undefined) last = frame.event_id;
          onFrame(frame as EventMsg);
        }, controller.signal);
        info = { code: 0, reason: '', retryable: true };
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      info = { code: 0, reason: error instanceof Error ? error.message : String(error), retryable: true };
    }
    opts.onClose?.(info);
    if (info.retryable) {
      retry = setTimeout(connect, wait);
      wait = Math.min(wait * 2, 10_000);
    }
  };
  void connect();
  return () => {
    controller.abort();
    if (retry) clearTimeout(retry);
  };
}
