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
  ProgressSourceRef,
  RequestUsage,
  Role,
  Snapshot,
  VerticalAction,
  VerticalManageResult,
  VerticalOperation,
  VerticalsPayload,
} from '../../core/src/types';
import { ApiError, ensureResponseOk } from '../../core/src/http';
import { readerPreview, type ReaderPreview } from './map/copyMode';
import { observePageRelease, requireCurrentPage } from './lib/pageUpdate';
import {
  requireCompatibleApiMeta,
  requireSnapshotContract,
  type ApiMeta,
} from '../../core/src/protocol';
import type { ResourceStatus } from '../../core/src/resourceStatus.generated';

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
  VerticalAction,
  VerticalCatalogStatus,
  VerticalKind,
  VerticalManageResult,
  VerticalOperation,
  VerticalRow,
  VerticalsPayload,
} from '../../core/src/types';
export type { ResourceStatus } from '../../core/src/resourceStatus.generated';

/** Advertised by GET /api/meta once the backend serves the vertical store. */
export const VERTICAL_STORE_CAPABILITY = 'verticals.store.v1';

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

/** One page of the project's shared Wiki, listed newest-first by the host. */
export interface WikiPageSummary {
  path: string;
  title: string;
  description: string;
  updated_at: number;
}
/** The project Wiki as the host sees it: absent, or INDEX.md plus its pages. */
export type WikiOverview =
  | { exists: false }
  | { exists: true; root: string; index_markdown: string; pages: WikiPageSummary[] };
/** One Wiki page body; the host caps the Markdown and flags the cut. */
export interface WikiPageDocument {
  path: string;
  title: string;
  description?: string;
  /** Page body without the front matter, when the host provides it. */
  content?: string;
  markdown: string;
  truncated: boolean;
  updated_at: number;
}

/** Where a knowledge page lives: shared by everyone, by one vertical, or kept by one project. */
export type WikiScope = 'private' | 'global' | 'vertical' | 'project';
/** What a knowledge page is: a fact, a lesson from reflection, a survey distilled after an answer, a set of principles, or a plain page. */
export type WikiPageKind = 'fact' | 'lesson' | 'survey' | 'principles' | 'note' | 'profile' | 'page';
/** One knowledge page flattened across libraries, newest first; the host adds scope, vertical and root. */
export interface WikiLibraryItem {
  scope: WikiScope;
  vertical: string;
  root: string;
  path: string;
  title: string;
  description: string;
  updated_at: number;
  /** Front-matter kind; the host falls back to "page" when a page names none. */
  kind: WikiPageKind | string;
  /** Where the page came from, e.g. "<project>/<mission>" or "chat/<session>"; empty when unknown. */
  source: string;
  /** ISO date the page was written; empty when unknown. */
  created: string;
  /** How many times the host handed this page to a role as recalled knowledge. */
  reuse_count: number;
}
/** One knowledge library (global, one vertical, or the project): INDEX.md plus its pages. */
export interface WikiLibrary {
  scope: WikiScope;
  vertical: string;
  root: string;
  index_markdown: string;
  pages: WikiPageSummary[];
  /** The library's principles.md, compiled from repeated lessons; null until there is one. */
  principles?: string | null;
  /** The operator's living profile (private scope only); null until Argus has written one. */
  profile?: string | null;
}
/** Every knowledge library the host can see for the given project, plus the flattened page list. */
export interface WikiCatalog {
  scopes: WikiScope[];
  libraries: WikiLibrary[];
  items: WikiLibraryItem[];
  verticals: string[];
  active_vertical: string;
  errors: string[];
}
/** One knowledge page: body without the front matter, raw text capped by the host. */
export interface WikiDocument {
  scope: WikiScope;
  vertical: string;
  path: string;
  title: string;
  description: string;
  content: string;
  markdown: string;
  truncated: boolean;
  updated_at: number;
}

/** One line of the host's knowledge journal: something learned, recalled into a prompt, or promoted to a shared level. */
export type KnowledgeEventKind = 'learned' | 'recalled' | 'promoted';
export interface KnowledgeEvent {
  ts: number;
  kind: KnowledgeEventKind;
  scope: WikiScope;
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

/** Status the host derived for one method component from the spec tests carrying its marker. */
export type ResearchMethodComponentStatus = 'proven' | 'contradicted' | 'partial' | 'untested' | 'unchecked';
/** One spec test joined to a component through its marker; outcome is null until the host has run it. */
export interface ResearchMethodTest {
  id: string;
  kind: string;
  outcome: string | null;
}
/** One component of the hand-written card; component/prescribes/notes are the agent's text, status/tests are derived. */
export interface ResearchMethodComponent {
  component: string;
  prescribes: string;
  notes: string;
  status: ResearchMethodComponentStatus;
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
/** A value read from a run config file; `why` is the Engineer's `# why:` comment, `previous` the last snapshot when changed. */
export interface ResearchMethodHyperparameter {
  key: string;
  value: string;
  file: string;
  why: string;
  changed: boolean;
  previous: string | null;
}
/** One git history entry touching the method. */
export interface ResearchMethodChange {
  when: string;
  summary: string;
  files: string[];
}
/** The latest host-run check round joined into the card. */
export interface ResearchMethodChecks {
  round_index: number;
  ran_at: number;
  exit_code: number | null;
  counts: Record<string, number>;
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
    checks: ResearchMethodChecks | null;
  };

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
  trial_mode?: boolean;
  generated_at_utc: string;
  roles: ConfigRole[];
  operator_knobs: ConfigKnob[];
  /** Models the quick picker offers: the harness catalog, models that answered here recently, the current knobs. */
  model_options?: Array<{ model: string; source: 'catalog' | 'seen' | 'current'; last_used_at?: number }>;
  how_to_change: string[];
}
export interface AdvisorConfig {
  schema_version: number;
  enabled: boolean;
  backend: string;
  model: string;
  effort: string;
  timeout_seconds: number;
  max_calls_per_turn: number;
  max_evidence_bytes: number;
}
export interface AdvisorSettings {
  saved: AdvisorConfig;
  config: AdvisorConfig;
  overridden_fields: string[];
  supported_backends: string[];
  model_options?: Array<{ backend: string; model: string }>;
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

async function postMultipart<T>(
  path: string,
  body: FormData,
  signal?: AbortSignal,
): Promise<T> {
  requireCurrentPage();
  const r = await fetch(path, {
    method: 'POST',
    headers: authHeaders(),
    body,
    signal,
  });
  await ensureResponseOk(r, 'POST', path);
  observePageRelease(r.headers.get('X-Argus-Release'));
  return (await r.json()) as T;
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
 * and scripts; the token rides in the query only when the app itself holds one,
 * since an iframe cannot send an auth header (a hosted portal adds it upstream,
 * and a localhost app needs none). */
export function previewPageUrl(sid: string, path: string): string {
  const params = new URLSearchParams({ path });
  const t = authToken();
  if (t) params.set('token', t);
  return P(sid, `/artifact/preview/page?${params.toString()}`);
}
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
          return requireCompatibleApiMeta(
            payload,
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

let activeSnapshotPrewarmSid: string | null = null;

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
async function explanationResponse<T>(path: string, body: unknown, signal?: AbortSignal, onProgress?: (phase: ExplanationPhase) => void): Promise<T> {
  const response = await postResponse(path, body, signal);
  let receivedTerminal = false;
  const terminal = await readSSE(response, 'Explanation stream', frame => {
    if (frame.type === 'done' || frame.type === 'error') receivedTerminal = true;
    if (!receivedTerminal && frame.type === 'progress' && EXPLANATION_PHASES.has(frame.phase))
      onProgress?.(frame.phase as ExplanationPhase);
  }, signal);
  if (terminal.type === 'error') {
    const detail = terminal.error && typeof terminal.error === 'object' ? terminal.error as Record<string, unknown> : undefined;
    throw new ApiError(String(detail?.message ?? terminal.error ?? 'Explanation failed'),
      typeof terminal.status === 'number' ? terminal.status : 0, 'POST', path,
      typeof detail?.code === 'string' ? detail.code : '');
  }
  return terminal.result as T;
}

export const api = {
  advisorSettings: (sid: string, signal?: AbortSignal) => getJson<AdvisorSettings>(P(sid, '/advisor/config'), signal),
  saveAdvisorSettings: (sid: string, config: Partial<Omit<AdvisorConfig, 'schema_version'>>) => postJson<AdvisorSettings>(P(sid, '/advisor/config'), config),
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
  mapInfo: (sid: string, signal?: AbortSignal) => getJson<import('./map/incremental').MapHistoryInfo>(P(sid, '/map-info'), signal),
  mapHistory: (sid: string, signal?: AbortSignal, after?: string, taskAfter?: string) => {
    const params = new URLSearchParams();
    if (after) params.set('after', after);
    if (taskAfter) params.set('task_after', taskAfter);
    return getJson<import('./map/model').Dataset>(P(sid, '/map-history') + (params.size ? `?${params}` : ''), signal);
  },
  mapCopy: (source: string, name: string, locale: string, signal?: AbortSignal, sessionId?: string, preview?: ReaderPreview, foundationId?: string | null) => getJson<import('./map/presentation').MapCopy>(mapCopyPath(source, name, { locale }, sessionId, preview, foundationId), signal),
  /** What the lines between tasks say. `write` also has the missing notes written. */
  mapLines: (source: string, name: string, body: { pairs: Array<{ source: string; target: string }>; locale: string; write: boolean }, signal?: AbortSignal, sessionId?: string) =>
    postJson<import('./map/useMapLines').MapLines>(`/api/map-lines/${source}/${encodeURIComponent(name)}${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`, body, signal),
  generateMapCopy: (source: string, name: string, body: {cards: import('./map/presentation').CardRequest[]; locale: string; foundation_id?: string}, signal?: AbortSignal, sessionId?: string, preview?: ReaderPreview, onProgress?: (phase: ExplanationPhase) => void): Promise<import('./map/presentation').MapCopy> =>
    explanationResponse(mapCopyPath(source, name, { stream: 'true' }, sessionId, preview, body.foundation_id), body, signal, onProgress),
  generateReaderFoundation: (sid: string, body: { request_id: string; question: string; locale: 'zh-CN' | 'en-US'; source_task_id?: string; progress_source?: Pick<ProgressSourceRef, 'source_id'> }, onProgress?: (phase: ExplanationPhase) => void): Promise<ArtifactInfo> =>
    explanationResponse(P(sid, '/reader-foundation?stream=true'), body, undefined, onProgress),
  askReaderFoundation: (sid: string, parentId: string, body: { request_id: string; question: string; locale: 'zh-CN' | 'en-US' }, onProgress?: (phase: ExplanationPhase) => void): Promise<ArtifactInfo> =>
    explanationResponse(P(sid, `/reader-foundation/${encodeURIComponent(parentId)}/question?stream=true`), body, undefined, onProgress),
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

  // Vertical store. Errors keep the service's own sentence in ApiError.detail,
  // so a 409 such as "used by projects s-…" can be shown on the card as written.
  verticals: (signal?: AbortSignal) =>
    getJson<VerticalsPayload>('/api/verticals', signal),
  refreshVerticalCatalog: () =>
    postJson<VerticalsPayload>('/api/verticals/catalog/refresh'),
  manageVertical: (name: string, action: VerticalAction, options: { force?: boolean } = {}) =>
    postJson<VerticalManageResult>(
      `/api/verticals/${encodeURIComponent(name)}/manage/${action}`,
      options.force ? { force: true } : {},
    ),
  verticalOperation: (name: string, signal?: AbortSignal) =>
    getJson<VerticalOperation>(`/api/verticals/${encodeURIComponent(name)}/operation`, signal),

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
    return postJson<{ kind: 'chat' | 'task' | 'pending_question' | 'pending_question_choice' | 'error' | 'cancelled'; reply: string | null; resolved?: boolean; item?: BacklogItem | null; daemon_alive?: boolean }>(
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
      onDelta?: (block: string, messageId: string, fragmentMode: string) => void;
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
  setConfig: (sid: string, name: string, value: string, applyToRoles = false) =>
    postJson<Record<string, unknown>>(P(sid, '/config/set'), applyToRoles ? { name, value, apply_to_roles: true } : { name, value }),
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
  skillLibrary: (sid: string | null, signal?: AbortSignal) =>
    getJson<SkillCatalog>(`/api/skill-library${sid ? `?sid=${encodeURIComponent(sid)}` : ''}`, signal),
  skillDocument: (sid: string | null, library: string, path: string, signal?: AbortSignal) =>
    getJson<SkillDocument>(`/api/skill-library/document?${new URLSearchParams({ library, path, ...(sid ? { sid } : {}) })}`, signal),
  researchMethod: (sid: string, signal?: AbortSignal) => getJson<ResearchMethod>(P(sid, '/research/method'), signal),
  wiki: (sid: string, signal?: AbortSignal) => getJson<WikiOverview>(P(sid, '/wiki'), signal),
  wikiPage: (sid: string, path: string, signal?: AbortSignal) =>
    getJson<WikiPageDocument>(P(sid, `/wiki/page?${new URLSearchParams({ path })}`), signal),
  wikiLibrary: (sid: string | null, signal?: AbortSignal) =>
    getJson<WikiCatalog>(`/api/wiki${sid ? `?sid=${encodeURIComponent(sid)}` : ''}`, signal),
  wikiDocument: (sid: string | null, scope: WikiScope, vertical: string, path: string, signal?: AbortSignal) =>
    getJson<WikiDocument>(`/api/wiki/page?${new URLSearchParams({ scope, vertical, path, ...(sid ? { sid } : {}) })}`, signal),
  knowledgeFeed: (limit = 50, signal?: AbortSignal) =>
    getJson<KnowledgeFeed>(`/api/knowledge/feed?${new URLSearchParams({ limit: String(limit) })}`, signal),
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
  ) => postJson(P(sid, '/daemon/stop'), {
    drain,
    force,
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
