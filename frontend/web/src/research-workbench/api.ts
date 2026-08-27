import type {
  ArtifactInfo,
  CounterexampleDashboard,
  EventMsg,
  GitDiffView,
  JournalEntry,
  ManagerResult,
  ProjectIndex,
  PromptRewrite,
  Snapshot,
  StatusView,
  Turn,
} from './types';
import { authHeaders, authToken, compatibleApiMeta, requestWithTimeout } from '../api';

const LOCAL_READ_TIMEOUT_MS = 12_000;

export function apiAuthHeaders(json = false): Record<string, string> {
  const value: Record<string, string> = { ...authHeaders() };
  if (json) value['Content-Type'] = 'application/json';
  return value;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  await compatibleApiMeta();
  const requestInit: RequestInit = {
    ...init,
    headers: { ...apiAuthHeaders(Boolean(init.body)), ...(init.headers ?? {}) },
    cache: 'no-store',
  };
  const method = String(init.method ?? 'GET').toUpperCase();
  const consume = async (response: Response): Promise<T> => {
    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      let message = detail;
      try {
        const parsed = JSON.parse(detail) as { detail?: string };
        message = parsed.detail ?? detail;
      } catch {
        // Keep plain-text detail.
      }
      throw new Error(message || `${init.method ?? 'GET'} ${path} failed (${response.status})`);
    }
    return (await response.json()) as T;
  };
  return method === 'GET'
    ? requestWithTimeout(path, requestInit, LOCAL_READ_TIMEOUT_MS, consume)
    : consume(await fetch(path, requestInit));
}

const projectPath = (sid: string, suffix = '') =>
  `/api/projects/${encodeURIComponent(sid)}${suffix}`;
const commandId = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

export interface MessageStreamHandlers {
  onPhase?: (label: string, role: string, detail: string, heartbeat: boolean) => void;
  onDelta?: (text: string, mode: string) => void;
  onDone?: (result: ManagerResult) => void;
}

function parseFrames(buffer: string): { frames: Array<Record<string, unknown>>; rest: string } {
  const normalized = buffer.replaceAll('\r\n', '\n');
  const parts = normalized.split('\n\n');
  const rest = parts.pop() ?? '';
  const frames: Array<Record<string, unknown>> = [];
  parts.forEach((part) => {
    part.split('\n').forEach((line) => {
      if (!line.startsWith('data:')) return;
      try {
        const parsed = JSON.parse(line.slice(5).trim()) as Record<string, unknown>;
        frames.push(parsed);
      } catch {
        // Ignore one malformed frame and keep the stream alive.
      }
    });
  });
  return { frames, rest };
}

function dispatchFrame(frame: Record<string, unknown>, handlers: MessageStreamHandlers): ManagerResult | null {
  const type = String(frame.type ?? '');
  if (type === 'phase') {
    handlers.onPhase?.(
      String(frame.label ?? ''),
      String(frame.role ?? 'manager'),
      String(frame.detail ?? ''),
      frame.heartbeat === true,
    );
  } else if (type === 'delta') {
    handlers.onDelta?.(String(frame.text ?? ''), String(frame.fragment_mode ?? 'auto'));
  } else if (type === 'done') {
    const result = (frame.result ?? {}) as ManagerResult;
    handlers.onDone?.(result);
    return result;
  } else if (type === 'error') {
    throw new Error(String(frame.error ?? 'Manager stream failed'));
  }
  return null;
}

export const api = {
  projects: (signal?: AbortSignal) => request<ProjectIndex>('/api/projects', { signal }),

  snapshot: (sid: string, signal?: AbortSignal) =>
    request<Snapshot>(
      projectPath(sid, '/snapshot?events_limit=40&compact=false'),
      { signal },
    ),

  status: (sid: string, signal?: AbortSignal) =>
    request<StatusView>(projectPath(sid, '/status'), { signal }),

  events: (sid: string, limit = 180, signal?: AbortSignal) =>
    request<{ events: EventMsg[] }>(projectPath(sid, `/events?limit=${limit}&view=ui`), { signal })
      .then((value) => value.events),

  transcript: (sid: string, limit = 100, signal?: AbortSignal) =>
    request<{ turns: Turn[] }>(projectPath(sid, `/transcript?n=${limit}`), { signal })
      .then((value) => value.turns),

  journal: (sid: string, limit = 80, signal?: AbortSignal) =>
    request<{ journal: JournalEntry[] }>(projectPath(sid, `/journal?n=${limit}`), { signal })
      .then((value) => value.journal),

  artifacts: (sid: string, signal?: AbortSignal) =>
    request<{ artifacts: ArtifactInfo[] }>(projectPath(sid, '/artifacts'), { signal })
      .then((value) => value.artifacts),

  counterexamples: (sid: string, signal?: AbortSignal) =>
    request<CounterexampleDashboard>(projectPath(sid, '/counterexamples'), { signal }),

  artifact: (sid: string, path: string, signal?: AbortSignal) =>
    request<ArtifactInfo>(projectPath(sid, `/artifact?${new URLSearchParams({ path })}`), { signal }),

  artifactBlob: async (sid: string, path: string, download = false, signal?: AbortSignal) => {
    await compatibleApiMeta();
    const params = new URLSearchParams({ path });
    if (download) params.set('download', 'true');
    const requestPath = projectPath(sid, `/artifact/raw?${params}`);
    return requestWithTimeout(requestPath, {
      headers: apiAuthHeaders(),
      signal,
      cache: 'no-store',
    }, LOCAL_READ_TIMEOUT_MS, async (response) => {
      if (!response.ok) throw new Error(`Artifact unavailable (${response.status})`);
      return response.blob();
    });
  },

  gitDiff: (sid: string, signal?: AbortSignal) =>
    request<GitDiffView>(projectPath(sid, '/git-diff'), { signal }),

  rewritePrompt: (sid: string, text: string, signal?: AbortSignal) =>
    request<PromptRewrite>(projectPath(sid, '/prompt/rewrite'), {
      method: 'POST',
      body: JSON.stringify({ text }),
      signal,
    }),

  note: (sid: string, text: string) =>
    request<Record<string, unknown>>(projectPath(sid, '/note'), {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  uploadAttachments: async (sid: string, files: File[], signal?: AbortSignal) => {
    await compatibleApiMeta();
    const form = new FormData();
    files.forEach((file) => form.append('files', file, file.name));
    const response = await fetch(projectPath(sid, '/attachments'), {
      method: 'POST',
      headers: apiAuthHeaders(),
      body: form,
      signal,
    });
    if (!response.ok) throw new Error(await response.text() || `attachment upload failed (${response.status})`);
    return await response.json() as {
      attachments: Array<{ attachment_id: string; original_name: string; size_bytes: number; mime: string }>;
    };
  },

  createDaemon: (objective: string, name = '', workdir = '') =>
    request<{ sid: string; rc: number; daemon: Record<string, unknown>; objective: string; workdir: string }>('/api/daemons', {
      method: 'POST',
      body: JSON.stringify({ objective, name, workdir, command_id: commandId() }),
    }),

  createFinalReview: (sid: string, input: {
    venue: string;
    venue_type: 'conference' | 'journal' | 'workshop';
    strictness: 'preflight' | 'standard' | 'strict' | 'red-team';
    manuscript_path: string;
    emphasis: string[];
    scope: string;
  }) => request<{ ok: boolean; manifest_path: string; manifest: Record<string, unknown>; dispatch: Record<string, unknown> }>(projectPath(sid, '/reviews/final'), {
    method: 'POST',
    body: JSON.stringify(input),
  }),

  startDaemon: (sid: string, expectedRevision?: number) =>
    request<Record<string, unknown>>(projectPath(sid, '/daemon/start'), {
      method: 'POST',
      body: JSON.stringify({ command_id: commandId(), expected_revision: expectedRevision }),
    }),

  stopDaemon: (sid: string, drain: boolean, expectedRevision?: number) =>
    request<Record<string, unknown>>(projectPath(sid, '/daemon/stop'), {
      method: 'POST',
      body: JSON.stringify({
        drain,
        command_id: commandId(),
        expected_revision: expectedRevision,
      }),
    }),

  async messageStream(
    sid: string,
    text: string,
    handlers: MessageStreamHandlers = {},
    signal?: AbortSignal,
    attachments: Array<{ attachment_id: string }> = [],
  ): Promise<ManagerResult> {
    await compatibleApiMeta();
    const path = projectPath(sid, '/message/stream');
    const response = await fetch(path, {
      method: 'POST',
      headers: apiAuthHeaders(true),
      body: JSON.stringify(attachments.length ? { text, attachments } : { text }),
      signal,
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      throw new Error(detail || `Manager request failed (${response.status})`);
    }
    if (!response.body) throw new Error('Manager returned an empty stream');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result: ManagerResult = {};
    for (;;) {
      const part = await reader.read();
      if (part.done) break;
      buffer += decoder.decode(part.value, { stream: true });
      const parsed = parseFrames(buffer);
      buffer = parsed.rest;
      parsed.frames.forEach((frame) => {
        const done = dispatchFrame(frame, handlers);
        if (done) result = done;
      });
    }
    const tail = parseFrames(`${buffer}\n\n`);
    tail.frames.forEach((frame) => {
      const done = dispatchFrame(frame, handlers);
      if (done) result = done;
    });
    return result;
  },
};

export interface StreamState {
  close: () => void;
}

export function openEventStream(
  sid: string,
  onEvent: (event: EventMsg) => void,
  onConnection: (connected: boolean) => void,
): StreamState {
  let closed = false;
  let socket: WebSocket | null = null;
  let retry: number | undefined;
  let delay = 800;

  const connect = () => {
    if (closed) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const params = new URLSearchParams({ replay: '40', view: 'ui' });
    const auth = authToken();
    if (auth) params.set('token', auth);
    socket = new WebSocket(
      `${protocol}//${window.location.host}${projectPath(sid, '/stream')}?${params}`,
    );
    socket.onopen = () => {
      delay = 800;
      onConnection(true);
    };
    socket.onmessage = (message) => {
      try {
        onEvent(JSON.parse(String(message.data)) as EventMsg);
      } catch {
        // Ignore malformed event frames.
      }
    };
    socket.onerror = () => socket?.close();
    socket.onclose = (event) => {
      onConnection(false);
      if (closed || event.code === 4401 || event.code === 4404) return;
      retry = window.setTimeout(connect, delay);
      delay = Math.min(delay * 1.7, 8_000);
    };
  };

  connect();
  return {
    close: () => {
      closed = true;
      if (retry) window.clearTimeout(retry);
      socket?.close();
    },
  };
}
