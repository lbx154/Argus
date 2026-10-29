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
  ProjectCostRow,
  ProgressSourceRef,
  Snapshot,
} from '../../core/src/types';
import { ApiError, ensureResponseOk } from '../../core/src/http';
import { readerPreview, type ReaderPreview } from './map/copyMode';
import { observePageRelease, requireCurrentPage } from './lib/pageUpdate';
import {
  RELEASE_ARTIFACT_DRIFT_WARNING,
  requireCompatibleApiMeta,
  requireSnapshotContract,
  type ApiMeta,
} from '../../core/src/protocol';
import type { ResourceStatus } from '../../core/src/resourceStatus.generated';
import { workflowClient } from '../../core/src/applicationWorkflows';
import {
  attachmentMime, attachmentSuffix, MESSAGE_ATTACHMENT_MAX_BYTES,
  MESSAGE_ATTACHMENT_MAX_COUNT, MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES,
} from './lib/attachments';

export type {
  ArtifactInfo,
  BacklogItem,
  Daemon,
  EventMsg,
  ProjectRow,
  ProjectCostRow,
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

/** One spec test joined to a component through its marker. */
export interface ResearchMethodTest {
  id: string;
  kind: string;
}
/** One component of the hand-written card; component/prescribes/notes are the agent's text, tests are derived. */
export interface ResearchMethodComponent {
  component: string;
  prescribes: string;
  notes: string;
  tests: ResearchMethodTest[];
}
/** A third_party/ clone or installed package the project's own code imports, found by the host's import scan. */
export interface ResearchMethodReusedCode {
  name: string;
  kind: 'third_party' | 'package';
  revision_or_version: string;
  remote: string;
  modules: string[];
  imported_from: string[];
}
/** A value read from a run config file; `why` is the Engineer's `# why:` comment. */
export interface ResearchMethodHyperparameter {
  key: string;
  value: string;
  file: string;
  why: string;
}
/** One git history entry touching the method. */
export interface ResearchMethodChange {
  when: string;
  summary: string;
  files: string[];
}
/**
 * A research project's method card: the hand-written METHOD.md plus what the
 * host derived from code, tests, config files and git at zero model cost.
 */
export type ResearchMethod =
  | { exists: false; error?: string }
  | {
    exists: true;
    path: string;
    /** METHOD.md mtime: epoch seconds or an ISO-8601 string; null when it could not be read. */
    updated_at: number | string | null;
    title: string;
    /** First paragraph after the H1. */
    statement: string;
    markdown: string;
    truncated: boolean;
    components: ResearchMethodComponent[];
    /** Components that carry test markers but are not named in the card. */
    unlisted_components?: string[];
    protocol: string;
    falsifiers: string;
    reused_code: ResearchMethodReusedCode[];
    hyperparameters: ResearchMethodHyperparameter[];
    change_log: ResearchMethodChange[];
  };

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
/** What a server without POLL_CAPABILITY pushes: the project list as the page connects, then each
 * project's state as it changes; a deleted project's is null. */
export type ProjectState =
  | ({ type: 'projects' } & ProjectIndex)
  | { type: 'project'; id: string; row: ProjectRow | null; snapshot: Snapshot | null };
export interface ProjectCostIndex {
  projects: ProjectCostRow[];
  generated_at: number;
}
/** A Manager-authored restatement of an operator draft (see the rewrite button). */
export interface PromptRewrite {
  original: string;
  rewritten: string;
  changes: string[];
  questions: string[];
  error: string;
  error_code?: string;
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
export interface ContinuousUpdateResult {
  ok: boolean;
  daemon?: {
    rc?: number;
    command_status?: string;
    error?: string;
    admission_required?: boolean;
  };
}
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
export interface TrashEntry {
  trash_id: string;
  sid: string;
  label: string;
  launch_cwd: string;
  trash_path: string;
  trashed_at: number;
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
  if (response.ok) {
    observePageRelease(response.headers.get('X-Argus-Release'));
    // Do not decode a newer snapshot with the old UI schema. Mutation
    // receipts still belong to their accepted request and must be delivered.
    if (!init.method || init.method === 'GET') requireCurrentPage();
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

async function postResponse(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  requireCurrentPage();
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  await ensureResponseOk(r, 'POST', path);
  observePageRelease(r.headers.get('X-Argus-Release'));
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
  requireCurrentPage();
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
    observePageRelease(response.headers.get('X-Argus-Release'));
    return await response.json() as T;
  });
}

function uploadSmallAttachments(
  path: string, files: File[], signal?: AbortSignal,
  onProgress?: AttachmentUploadOptions['onProgress'],
): Promise<AttachmentUploadResponse> {
  requireCurrentPage();
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
        observePageRelease(xhr.getResponseHeader('X-Argus-Release'));
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
  requireCurrentPage();
  const r = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  await ensureResponseOk(r, method, path);
  observePageRelease(r.headers.get('X-Argus-Release'));
  return (await r.json()) as T;
}

async function getBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const r = await fetch(path, { headers: authHeaders(), signal });
  await ensureResponseOk(r, 'GET', path);
  return r.blob();
}

const P = (sid: string, path = '') => `/api/projects/${encodeURIComponent(sid)}${path}`;

/** URL of the served, self-sandboxing preview page for an HTML result. A
 * sandboxed iframe loads it by URL so the delivered site keeps its own styles
 * and scripts; it cannot send an auth header, so it presents the cookie that
 * `api.artifactPreview` received (a hosted portal adds the header upstream). */
export const previewPageUrl = (sid: string, path: string): string =>
  P(sid, `/artifact/preview/page?${new URLSearchParams({ path })}`);
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
          observePageRelease(payload?.runtime?.release_id);
          return requireCompatibleApiMeta(payload, (warning) => {
            // Release drift is a publisher's note: the served bundle already
            // matches this API (the validator would have thrown otherwise),
            // so a reader's console has nothing to act on.
            if (warning === RELEASE_ARTIFACT_DRIFT_WARNING) return;
            console.warn(`Argus API compatibility warning: ${warning}`);
          });
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

export type ExplanationPhase = 'waiting_for_source' | 'planning' | 'writing' | 'reviewing';
const EXPLANATION_PHASES = new Set<unknown>(['waiting_for_source', 'planning', 'writing', 'reviewing']);

/** The final ``done`` frame payload — same shape as blocking ``message()``. */
export interface StreamDone {
  kind?: string;
  reply?: string | null;
  item?: BacklogItem | null;
  /** Tool steps journaled with the reply (see core/phaseTrail TurnStep). */
  steps?: unknown;
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

/** An explicitly selected preview uses the normal web request and its own cache. */
function mapCopyPath(source: string, name: string, values: Record<string, string>, sessionId?: string, preview = readerPreview(), foundationId?: string | null): string {
  const params = new URLSearchParams(values);
  if (sessionId) params.set('session_id', sessionId);
  if (preview === 'source-first') params.set('preview', 'true');
  if (preview === 'learning-path') params.set('preview', 'learning-path');
  if (preview === 'question-foundation') {
    params.set('preview', 'question-foundation');
    if (foundationId) params.set('foundation_id', foundationId);
  }
  return `/api/map-copy/${source}/${encodeURIComponent(name)}?${params}`;
}

/** Both explanation purposes use the same stream and terminal/EOF semantics. */
async function explanationResponse<T>(path: string, body: unknown, onProgress?: (phase: ExplanationPhase) => void): Promise<T> {
  const response = await postResponse(path, body);
  let receivedTerminal = false;
  const terminal = await readSSE(response, 'Explanation stream', frame => {
    if (frame.type === 'done' || frame.type === 'error') receivedTerminal = true;
    if (!receivedTerminal && frame.type === 'progress' && EXPLANATION_PHASES.has(frame.phase))
      onProgress?.(frame.phase as ExplanationPhase);
  });
  if (terminal.type === 'error') {
    const detail = terminal.error && typeof terminal.error === 'object' ? terminal.error as Record<string, unknown> : undefined;
    throw new ApiError(String(detail?.message ?? terminal.error ?? 'Explanation failed'),
      typeof terminal.status === 'number' ? terminal.status : 0, 'POST', path,
      typeof detail?.code === 'string' ? detail.code : '');
  }
  return terminal.result as T;
}

export interface ProjectPreference { pinned: boolean; archived: boolean; followup: boolean }
export interface Edition { version: number; content: string; note: string; created: number }
export interface SearchResult {
  sid: string; title: string; kind: 'project' | 'conversation' | 'artifact' | 'edition';
  snippet: string; text: string; path?: string; version?: number; role?: string;
  ts?: number; line?: number; message_id?: string; truncated?: boolean;
}

export const api = {
  organization: () => getJson<{ value: Record<string, ProjectPreference>; revision: number }>('/api/browser-state/organization'),
  organize: (sid: string, field: keyof ProjectPreference, value: boolean) =>
    postJson<{ value: Record<string, ProjectPreference>; revision: number }>('/api/browser-state/organization', { sid, field, value }),
  searchHistory: (query: string, signal?: AbortSignal) =>
    getJson<{ results: SearchResult[]; partial: boolean; errors: string[] }>(`/api/search?q=${encodeURIComponent(query)}`, signal, 30_000),
  editions: (sid: string, kind: 'artifact' | 'conversation', source: string) =>
    getJson<{ original: string; versions: Edition[] }>(P(sid, `/editions/${kind}?source=${encodeURIComponent(source)}`)),
  saveEdition: (sid: string, kind: 'artifact' | 'conversation', value: { source: string; content: string; note?: string; base_version: number; original?: string }) =>
    postJson<{ version: number; content: string }>(P(sid, `/editions/${kind}`), value),
  /** What `content` changes in the text result `source` as it is now, as a unified diff. */
  editionDiff: (sid: string, source: string, content: string) =>
    postJson<{ diff: string; truncated: boolean }>(P(sid, '/editions/artifact/diff'), { source, content }),
  workflows: workflowClient(getJson, postJson),
  liveMap: (sid: string, signal?: AbortSignal, after?: string, selection?: import('./map/incremental').MapSelection) => {
    const params = new URLSearchParams();
    if (after) params.set('after', after);
    if (selection?.mode === 'current') {
      params.set('since', String(selection.since));
      params.set('event_since', String(selection.eventSince));
      if (selection.taskId) params.set('start_task', selection.taskId);
    }
    return getJson<import('./map/model').Dataset>(P(sid, '/map') + (params.size ? `?${params}` : ''), signal);
  },
  mapCopy: (source: string, name: string, locale: string, signal?: AbortSignal, sessionId?: string, preview?: ReaderPreview, foundationId?: string | null) => getJson<import('./map/presentation').MapCopy>(mapCopyPath(source, name, { locale }, sessionId, preview, foundationId), signal),
  /** One task's recorded trajectory: what each role thought, said, ran and changed. */
  mapTrace: (sid: string, task: string, signal?: AbortSignal) =>
    getJson<import('./components/TaskTrace').TaskTraceData>(P(sid, '/map-trace') + `?task=${encodeURIComponent(task)}`, signal),
  /** The explanation a reader is about to ask about, kept at that moment with the step's records. */
  mapQuestionSource: (sid: string, body: { card_key: string; task_id: string; locale: 'zh-CN' | 'en-US'; preview?: ReaderPreview; foundation_id?: string }, signal?: AbortSignal) =>
    postJson<ProgressSourceRef>(`/api/map-question-source/project/${encodeURIComponent(sid)}?session_id=${encodeURIComponent(sid)}`,
      { ...body, preview: body.preview === 'source-first' ? true : body.preview ?? false }, signal),
  generateReaderFoundation: (sid: string, body: { request_id: string; question: string; locale: 'zh-CN' | 'en-US'; source_task_id?: string; progress_source?: Pick<ProgressSourceRef, 'source_id'> }, onProgress?: (phase: ExplanationPhase) => void): Promise<ArtifactInfo> =>
    explanationResponse(P(sid, '/reader-foundation?stream=true'), body, onProgress),
  askReaderFoundation: (sid: string, parentId: string, body: { request_id: string; question: string; locale: 'zh-CN' | 'en-US' }, onProgress?: (phase: ExplanationPhase) => void): Promise<ArtifactInfo> =>
    explanationResponse(P(sid, `/reader-foundation/${encodeURIComponent(parentId)}/question?stream=true`), body, onProgress),
  mapDatasets: (signal?: AbortSignal) => getJson<{ datasets: import('./map/model').DatasetSummary[] }>('/api/map-datasets', signal),
  mapDataset: (id: string, signal?: AbortSignal) => getJson<import('./map/model').Dataset>(`/api/map-datasets/${encodeURIComponent(id)}`, signal),
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
  snapshot: async (sid: string, signal?: AbortSignal) => {
    await compatibleApiMeta();
    const value = await getJson<unknown>(
      P(sid, '/snapshot?compact=true&events_limit=1'),
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
  transcript: (sid: string, n = 30, signal?: AbortSignal) =>
    getJson<{ turns: Turn[] }>(P(sid, `/transcript?n=${n}`), signal)
      .then((r) => r.turns),
  events: (sid: string, limit = 80, signal?: AbortSignal) =>
    getJson<{ events: EventMsg[] }>(
      P(sid, `/events?limit=${limit}&view=ui`),
      signal,
    )
      .then((r) => r.events),
  workRecord: (sid: string, role: string, recordId: string, signal?: AbortSignal) =>
    getJson<{ id: string; role: string; detail: string; characters: number }>(
      P(sid, `/work-record?role=${encodeURIComponent(role)}&record_id=${encodeURIComponent(recordId)}`), signal,
    ),
  downloadWorkLog: async (sid: string, role: string, taskId = '') => {
    const path = P(sid, `/work-log/download?role=${encodeURIComponent(role)}&task_id=${encodeURIComponent(taskId)}`);
    const response = await fetch(path, { headers: authHeaders(), cache: 'no-store' });
    await ensureResponseOk(response, 'GET', path);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url; link.download = 'work-records.md'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  backlogItem: (sid: string, id: string, signal?: AbortSignal) =>
    getJson<{ item: BacklogItem }>(
      P(sid, `/backlog/${encodeURIComponent(id)}`),
      signal,
    ).then((r) => r.item),
  artifacts: (sid: string, signal?: AbortSignal, includeReading = false) =>
    getJson<{ artifacts: ArtifactInfo[] }>(P(sid, `/artifacts${includeReading ? '?include_reading=true' : ''}`), signal)
      .then((r) => r.artifacts),
  artifact: (sid: string, path: string, signal?: AbortSignal) => {
    const q = new URLSearchParams({ path });
    return getJson<ArtifactInfo>(P(sid, `/artifact?${q}`), signal);
  },
  artifactPreview: (sid: string, path: string, signal?: AbortSignal) =>
    getJson<{ html: string; warnings: string[]; file_count: number; served_page?: boolean }>(
      P(sid, `/artifact/preview?${new URLSearchParams({ path })}`), signal),
  /** One zip of listed results (each path passes the artifact allowlist). */
  artifactsBundle: async (sid: string, paths: string[], name = '', summary = '', signal?: AbortSignal) =>
    (await postResponse(P(sid, '/artifacts/bundle'), { paths, name, summary }, signal)).blob(),
  artifactBundle: (sid: string, path: string, signal?: AbortSignal) =>
    getBlob(P(sid, `/artifact/bundle?${new URLSearchParams({ path })}`), signal),
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
  artifactRawUrl: (sid: string, path: string) =>
    P(sid, `/artifact/raw?${new URLSearchParams({ path })}`),
  /** A mesh, glTF buffer or texture a registered model references, by the reference as written. */
  artifactModelAsset: (sid: string, path: string, ref: string, signal?: AbortSignal) =>
    getBlob(P(sid, `/artifact/model-asset?${new URLSearchParams({ path, ref })}`), signal),
  deliveryDiff: (sid: string, change: { path: string; before: string; after: string }, signal?: AbortSignal) =>
    getJson<{ path: string; available: boolean; diff: string; truncated: boolean }>(
      P(sid, `/delivery-diff?${new URLSearchParams(change)}`), signal),
  resources: (signal?: AbortSignal) =>
    getJson<ResourceStatus>('/api/system/resources', signal),
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
  mapNotes: (sid: string, signal?: AbortSignal) =>
    getJson<{ notes: import('./map/notes').MapNote[] }>(P(sid, '/map-notes'), signal),
  addMapNote: (sid: string, body: { node_id: string; text: string; author?: string }) =>
    postJson<{ note: import('./map/notes').MapNote }>(P(sid, '/map-notes'), body),
  mapCanvas: (sid: string, signal?: AbortSignal) =>
    getJson<import('./map/canvas').CanvasRecord>(P(sid, '/canvas'), signal),
  /** One canvas write; the body carries the revision it was based on. */
  writeMapCanvas: <T extends { canvas: import('./map/canvas').CanvasRecord }>(
    sid: string, method: 'POST' | 'PATCH' | 'PUT' | 'DELETE', path: string, body?: Record<string, unknown>,
  ) => uploadRequest<T>(method, P(sid, `/canvas${path}`), body),
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
    options: AttachmentUploadOptions = {},
  ): Promise<AttachmentUploadResponse> => {
    await compatibleApiMeta();
    signal?.throwIfAborted();
    const attachments = [...(options.ready ?? [])];
    if (files.length + attachments.length > MESSAGE_ATTACHMENT_MAX_COUNT
      || files.some(file => file.size > MESSAGE_ATTACHMENT_MAX_BYTES)
      || files.reduce((sum, file) => sum + file.size, attachments.reduce((sum, file) => sum + file.size_bytes, 0)) > MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES) {
      throw new Error('Attachments exceed the server file/count/total limits');
    }
    const fresh = files.filter(file => !options.completed?.has(file));
    if (fresh.length === 0) {
      for (const file of files) {
        const attachment = options.completed?.get(file);
        if (!attachment) throw new Error('Missing completed attachment receipt');
        attachments.push(attachment);
      }
      return { attachments, limits: {
        max_count: MESSAGE_ATTACHMENT_MAX_COUNT, max_bytes_per_file: MESSAGE_ATTACHMENT_MAX_BYTES,
        max_total_bytes: MESSAGE_ATTACHMENT_TOTAL_MAX_BYTES,
      } };
    }
    // Preserve streamed JSON resource limits and existing partial upload identities.
    if (fresh.length > 0
      && fresh.every(file => file.size <= 10 * 1024 * 1024 && attachmentSuffix(file.name) !== '.json'
        && !pendingUploads.get(file)?.has(sid))
      && fresh.reduce((sum, file) => sum + file.size, 0) <= 25 * 1024 * 1024) {
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
    const limits = await getJson<AttachmentUploadResponse['limits'] & { chunk_bytes: number }>(
      P(sid, '/attachments/limits'), signal,
    );
    if (![limits.max_count, limits.max_bytes_per_file, limits.max_total_bytes, limits.chunk_bytes]
      .every(value => Number.isSafeInteger(value) && value > 0) || limits.chunk_bytes > 4 * 1024 * 1024) {
      throw new Error('Invalid attachment upload limits from server');
    }
    if (files.length + attachments.length > limits.max_count
      || files.some(file => file.size > limits.max_bytes_per_file)
      || files.reduce((sum, file) => sum + file.size, attachments.reduce((sum, file) => sum + file.size_bytes, 0)) > limits.max_total_bytes) {
      throw new Error('Attachments exceed the server file/count/total limits');
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
  answerDomain: (sid: string, id: string, optionId: string, note: string) =>
    postJson<{ kind: string; reply?: string; resolved?: boolean; daemon?: { rc?: number; error?: string } }>(
      P(sid, '/message'), { text: note || optionId, domain_answer: { id, option_id: optionId, note } },
    ),
  /** The Manager front-door: NL message → chat reply or an enqueued mission. */
  message: (
    sid: string,
    text: string,
    signalOrOptions?: AbortSignal | {
      signal?: AbortSignal;
      attachments?: MessageAttachmentRef[];
      routeOverride?: MessageRouteOverride;
      requestId?: string;
    },
  ) => {
    const signal = isAbortSignal(signalOrOptions) ? signalOrOptions : signalOrOptions?.signal;
    const attachments = isAbortSignal(signalOrOptions) ? undefined : signalOrOptions?.attachments;
    const routeOverride = isAbortSignal(signalOrOptions) ? undefined : signalOrOptions?.routeOverride;
    const requestId = isAbortSignal(signalOrOptions) ? undefined : signalOrOptions?.requestId;
    return postJson<{ kind: 'chat' | 'task' | 'pending_question' | 'error' | 'cancelled'; reply: string | null; resolved?: boolean; item?: BacklogItem | null; daemon_alive?: boolean }>(
      P(sid, '/message'),
      messageBody(text, attachments, routeOverride, requestId),
      signal,
    );
  },
  cancelMessage: (sid: string, requestId: string) =>
    requestWithTimeout(P(sid, '/message/cancel'), {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ request_id: requestId }),
    }, 5000, async (response) => {
      await ensureResponseOk(response, 'POST', P(sid, '/message/cancel'));
      return await response.json() as { requested: boolean; status: string };
    }),
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
        meta: {
          heartbeat: boolean;
          quietS: number;
          kind: string;
          detail: string;
          /** Plain title of the tool call, its runner-side id and how it ended. */
          tool: string;
          toolKind: string;
          callId: string;
          status: string;
          output: string;
        },
      ) => void;
      /** ``inProgress`` marks a snapshot of a reply still being written, not the final text. */
      onDelta?: (block: string, messageId: string, fragmentMode: string, inProgress: boolean) => void;
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
            tool: String(f.tool ?? ''),
            toolKind: String(f.tool_kind ?? ''),
            callId: String(f.call_id ?? ''),
            status: String(f.status ?? ''),
            output: String(f.output ?? ''),
          },
        );
      }
      else if (f.type === 'delta') {
        handlers.onDelta?.(
          String(f.text ?? ''),
          String(f.message_id ?? ''),
          String(f.fragment_mode ?? 'auto'),
          f.live === true,
        );
      }
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
  researchMethod: (sid: string, signal?: AbortSignal) => getJson<ResearchMethod>(P(sid, '/research/method'), signal),
  knowledgeFeed: (limit = 50, signal?: AbortSignal) =>
    getJson<KnowledgeFeed>(`/api/knowledge/feed?${new URLSearchParams({ limit: String(limit) })}`, signal),
  learningStatus: (sid: string, signal?: AbortSignal) => getJson<LearningState>(P(sid, '/learning'), signal),
  setLaunchCwd: (sid: string, launchCwd: string) =>
    postJson<{ ok: boolean }>(P(sid, '/launch-cwd'), { launch_cwd: launchCwd }),
  setWorkdir: (sid: string, workdir: string) =>
    postJson<{ ok: boolean; workdir: string; unchanged?: boolean }>(
      P(sid, '/workdir'),
      { workdir },
    ),
  setContinuous: (sid: string, enabled: boolean, objective = '') =>
    postJson<ContinuousUpdateResult>(P(sid, '/continuous'), { enabled, objective }).then((result) => {
      if (!enabled) return result;
      if (!result.daemon) throw new Error('daemon start returned no result');
      requireDaemonCommand(result.daemon);
      return result;
    }),
  startDaemon: (sid: string, expectedRevision?: number) => postJson(P(sid, '/daemon/start'), {
    command_id: commandId(),
    expected_revision: expectedRevision,
  }).then(requireDaemonCommand),
  stopDaemon: (
    sid: string,
    drain = false,
    expectedRevision?: number,
    force = false,
    afterCurrent = false,
  ) => postJson(P(sid, '/daemon/stop'), {
    drain,
    force,
    ...(afterCurrent ? { after_current: true } : {}),
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

/** Open the live event stream (server-sent events) for a project. Returns a close() fn. */
export function openStream(
  sid: string,
  onEvent: (ev: EventMsg) => void,
  opts: StreamOptions & { replay?: number } = {},
): () => void {
  const q = new URLSearchParams();
  if (opts.replay != null) q.set('replay', String(opts.replay));
  q.set('view', 'ui');
  return follow(`${P(sid, '/stream')}?${q}`, 'Event stream', onEvent, opts);
}

/** The project list, then each project's state as it changes, from a server without POLL_CAPABILITY. Returns a close() fn. */
export const openProjectStates = (onState: (state: ProjectState) => void, opts: StreamOptions = {}): (() => void) =>
  follow('/api/projects/stream', 'Project stream', (frame) => onState(frame as ProjectState), opts);

/** Follow server-sent events, connecting again a second after a lost connection; a record's
 * stream is told the last event the page has (Last-Event-ID) and goes on after it. */
function follow(path: string, label: string, onFrame: (frame: EventMsg) => void, opts: StreamOptions): () => void {
  const controller = new AbortController();
  let retry: ReturnType<typeof setTimeout> | undefined;
  let last: unknown;
  const connect = async () => {
    let info: StreamCloseInfo;
    try {
      const headers = last === undefined ? authHeaders() : { ...authHeaders(), 'Last-Event-ID': String(last) };
      const response = await fetch(path, { headers, signal: controller.signal });
      if (!response.ok) {
        info = { code: response.status, reason: response.statusText, retryable: !NON_RETRYABLE_STREAM_STATUSES.has(response.status) };
      } else {
        opts.onOpen?.();
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
    if (info.retryable) retry = setTimeout(connect, 1000); // reconnect with backoff
  };
  void connect();
  return () => {
    controller.abort();
    if (retry) clearTimeout(retry);
  };
}
