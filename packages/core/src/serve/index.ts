// `argus serve`: the web cockpit's API and its built pages, on 127.0.0.1, or with a token on any
// address. Requests read the projects' files as they are; messages, answers, starts and stops go
// to the Flow runs of this process. With a token (ARGUS_WEB_TOKEN) every project request needs it
// as a bearer. HTML results have their own static origin.

import { randomUUID, timingSafeEqual } from 'node:crypto';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  API_PROTOCOL, API_SERVICE, REQUIRED_API_CAPABILITIES, SNAPSHOT_SCHEMA_VERSION, type ApiMeta,
} from '@argus/contracts';
import corePackage from '../../package.json' with { type: 'json' };
import { rewritePrompt } from '../prompts.js';
import { credentials, saveCredentials } from '../credentials.js';
import { PROJECT_ID, asker, type Project, type Store } from '../store.js';
import type { Backend, CallAgent } from '../types.js';
import { attached, attachedFile, attachments } from './attachments.js';
import { atDelivery, artifactInfo, artifacts, modelAssets, rawHeaders, sendFile, sendStatic, under } from './files.js';
import { library } from './library.js';
import { preview } from './preview.js';
import { CAPABILITIES, directory, projectRoutes } from './projects.js';
import { records } from './records.js';
import { resources } from './resources.js';
import { setupStatus, mobileOrigins } from './setup.js';
import { Runs, type Frame } from './runs.js';
import { trash } from './trash.js';
import { versions } from './versions.js';
import { daemon, follow, progressMap, projectRow, snapshot, taskTrace, readRecord, conversationRows, conversationPage } from './views.js';
import { workspace } from './workspace.js';

export interface ServeOptions {
  store: Store;
  callAgent: CallAgent;
  backend: Backend;
  model: string | null;
  /** 0 picks a free port. */
  port: number;
  /** The static preview's public origin template; forwarding must preserve Host and Origin. */
  previewUrl?: string;
  /** The built web cockpit: the package's web/, the files of frontend/web/dist its page reaches. */
  web: string;
  /** The address to listen on, 127.0.0.1 by default; one beyond this machine needs a token. */
  host?: string;
  token?: string;
}

export interface Served { url: string; close(): Promise<void> }

const BRAND = { schema: 1, id: 'argus', name: 'Argus', tagline: 'Autonomous Work Lab', terminal_wordmark: 'argus', assets: {} };
const SOURCE_ROOT = fileURLToPath(new URL('../../', import.meta.url));
const STARTED = new Date().toISOString();
// A million-character message, escaped as \uXXXX by some clients, plus its JSON.
const BODY_LIMIT = 8 << 20;

export class HttpError extends Error {
  constructor(readonly status: number, message: string, readonly code = '') { super(message); }
}

export interface Context {
  request: IncomingMessage;
  response: ServerResponse;
  url: URL;
  /** The path's captures, decoded. */
  params: string[];
  body(): Promise<Record<string, unknown>>;
}

export type Handler = (context: Context) => unknown;

export async function serve(options: ServeOptions): Promise<Served> {
  const { store, token, host = '127.0.0.1' } = options;
  // Whoever reaches the cockpit can run work on this machine.
  if (!token && !/^(localhost|127(\.\d+){3}|::1)$/.test(host)) throw new Error(`Serving on ${host} needs ARGUS_WEB_TOKEN.`);
  /** The streams following each project's record, and those following every project's state. */
  const listeners = new Map<string, Set<(moved: boolean) => void>>();
  const watchers = new Map<(frame: Record<string, unknown>) => void, string | null>();
  /** Send each project's changes once the current event has run on to its next wait:
   * a question is added just after the letter that asks it is recorded. */
  const due = new Map<string, { moved: boolean; state: boolean }>();
  const runs = new Runs(store, options.callAgent, (id, event) => {
    // A step, or words as they are written, change no project's state.
    const quiet = event === 'words' || event?.type === 'step';
    if (!listeners.get(id)?.size && (!watchers.size || quiet)) return;
    // A command or an edit moves no row, only its role's log. A message changes
    // dialogue or task progress; words as they come are the reply's or the task's.
    const moved = event === 'words' || event?.type !== 'step' || event.kind === 'message';
    const pending = due.get(id);
    if (pending) {
      pending.moved ||= moved;
      pending.state ||= !quiet;
      return;
    }
    due.set(id, { moved, state: !quiet });
    // Words come a few at a time: they go out at most ten times a second.
    (event === 'words' ? (go: () => void) => setTimeout(go, 100) : setImmediate)(() => {
      const changed = due.get(id)!;
      due.delete(id);
      if (changed.state && watchers.size) {
        const frame = state(id);
        let selected: Record<string, unknown> | undefined;
        for (const [send, selectedId] of watchers) {
          send(frame);
          if (selectedId === id && frame.row) {
            selected ??= { type: 'snapshot', id, snapshot: snapshot(store, store.load(id), runs.live(id)) };
            send(selected);
          }
        }
      }
      // A deletion reaches the list before its record stream ends.
      for (const wake of listeners.get(id) ?? []) wake(changed.moved);
    });
  });

  const project = (id: string): Project => {
    try {
      if (PROJECT_ID.test(id)) return store.load(id);
    } catch { /* not a project */ }
    throw new HttpError(404, `unknown project: ${id}`);
  };
  /** A result's file as it is now; of a delivery, the copy it kept of the file, where it kept one. */
  const result = (work: Project, path: string, delivery?: string | null) => {
    const copy = delivery == null ? undefined
      : readRecord(store, work.id).delivered.get(delivery)?.snapshots?.find(file => file.path === path && file.stored);
    return (copy && atDelivery(store.dir(work.id), copy)) || under(work.workdir, path) || attachedFile(work, path);
  };
  const list = () => ({
    projects: store.list().map(project => projectRow(store, project, runs.live(project.id))).sort((a, b) => b.last_active - a.last_active),
    local_cwd: process.cwd(),
  });
  /** The list change every page needs; only readers of this project receive its snapshot. */
  const state = (id: string): Record<string, unknown> => {
    let work: Project;
    try { work = store.load(id); } catch { return { type: 'project', id, row: null }; }
    const live = runs.live(id);
    return { type: 'project', id, row: projectRow(store, work, live) };
  };
  const authorized = (request: IncomingMessage): boolean => !token
    || same(request.headers.authorization, `Bearer ${token}`);
  const sites = preview(store, project, result, host, options.previewUrl);
  const meta = (authenticated: boolean): ApiMeta => ({
    service: API_SERVICE,
    protocol: { name: API_PROTOCOL.name, major: API_PROTOCOL.major, minor: API_PROTOCOL.minServerMinor },
    snapshot_schema_version: SNAPSHOT_SCHEMA_VERSION,
    capabilities: [...REQUIRED_API_CAPABILITIES, 'project.attachments.v1', 'system.resources.v1', ...CAPABILITIES],
    runtime: {
      package_version: corePackage.version, pid: process.pid, started_at: STARTED,
      source_root: authenticated ? SOURCE_ROOT : '<redacted>', executable: authenticated ? process.execPath : '<redacted>',
      state_root: authenticated ? store.home : null,
    },
    authentication: { required: Boolean(token), authenticated },
  });
  /** Server-sent events, a record's with its line as their id; a frame for a reader who left is dropped. */
  const sse = (response: ServerResponse): ((frame: Record<string, unknown>) => void) => {
    response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', 'x-accel-buffering': 'no' }).flushHeaders();
    // A stream that goes quiet still learns when its reader has gone.
    response.socket?.setKeepAlive(true, 60_000);
    return frame => {
      if (!response.destroyed) response.write(`${'event_id' in frame ? `id: ${frame.event_id}\n` : ''}data: ${JSON.stringify(frame)}\n\n`);
    };
  };
  /** Each permanent reader checks the entry the server serves on this connection. */
  const connected = (send: (frame: Record<string, unknown>) => void): void => {
    let entry: string | null = null;
    try {
      entry = /<script\b[^>]*\btype="module"[^>]*\bsrc="([^"]+)"/.exec(readFileSync(join(options.web, 'index.html'), 'utf8'))?.[1] ?? null;
    } catch { /* An API-only server has no built entry. */ }
    send({ type: 'connected', entry, meta: meta(true) });
  };
  /** The operator's answer to one of the open questions, in their own words. */
  const answer = async (id: string, card: string, note: string, files?: unknown): Promise<void> => {
    const work = project(id);
    const question = work.questions.find(question => asker(question) === card);
    if (!question) throw new HttpError(409, 'This question is no longer open.', 'question_closed');
    if (!note.trim()) throw new HttpError(400, 'empty answer', 'input_required');
    const uploaded = attached(work, files);
    // Answered by another request meanwhile, while the work under way stopped.
    await runs.answer(id, question, note.trim(), uploaded).catch(() => { throw new HttpError(409, 'This question is no longer open.', 'question_closed'); });
  };
  /** The message a request carries, checked before any reply is under way. */
  const message = async ({ params: [id], body }: Context): Promise<(send: (frame: Frame) => void) => Promise<Frame>> => {
    const { text, request_id: request, attachments: files } = await body();
    if (typeof text !== 'string' || !text.trim()) throw new HttpError(400, 'empty message', 'input_required');
    const work = project(id!);
    const uploaded = attached(work, files);
    return send => runs.message(work.id, text, typeof request === 'string' && request ? request : randomUUID(), send, uploaded);
  };
  const command = (id: string, body: Record<string, unknown>) =>
    ({ rc: 0, command_id: body.command_id ?? '', command_status: 'applied', daemon: daemon(project(id), runs.live(id)) });

  const routes: [string, RegExp, Handler][] = [
    ...trash(store, runs, project), ...attachments(project), ...versions(store, project),
    ['GET', /^\/api\/settings\/credentials$/, ({ response }) => {
      response.setHeader('cache-control', 'no-store');
      return { names: Object.keys(credentials(store.home)).sort() };
    }],
    ['PUT', /^\/api\/settings\/credentials$/, async ({ body }) => {
      const { name, value } = await body();
      if (typeof name !== 'string' || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)
        || typeof value !== 'string' || value.includes('\0')) {
        throw new HttpError(400, 'Enter an environment variable name and a string value without NUL.', 'invalid_credential');
      }
      const values = { ...credentials(store.home), [name]: value };
      saveCredentials(store.home, values);
      return { names: Object.keys(values).sort() };
    }],
    ['DELETE', /^\/api\/settings\/credentials\/([^/]+)$/, ({ params: [name] }) => {
      const values = credentials(store.home);
      delete values[name!];
      saveCredentials(store.home, values);
      return { names: Object.keys(values).sort() };
    }],
    ['GET', /^\/api\/application-brand$/, () => BRAND],
    // The brand is Argus's own, so its manifest is the one the page was built with.
    ['GET', /^\/api\/application-brand\/manifest\.webmanifest$/, ({ request, response }) => {
      if (!sendStatic(request, response, options.web, '/manifest.webmanifest')) throw new HttpError(404, 'Not Found');
    }],
    ['GET', /^\/api\/projects$/, list],
    // Installation and version probes belong to Settings, never to a snapshot.
    ['GET', /^\/api\/system\/setup$/, async () => ({
      ...await setupStatus(), mobile_origins: mobileOrigins(host, (server.address() as AddressInfo).port),
    })],
    ['POST', /^\/api\/daemons$/, async ({ body }) => {
      const workdir = directory(String((await body()).workdir ?? ''));
      const recent = store.list().at(-1);
      const { backend, model } = recent ?? options;
      const modelAutomatic = recent?.modelAutomatic ?? model === null;
      const created = store.create({ workdir, request: '', backend, model: modelAutomatic ? null : model, modelAutomatic, ...recent?.effort ? { effort: recent.effort } : {} });
      runs.changed(created.id);
      return { sid: created.id, rc: 0, command_status: 'applied', objective: '', workdir: created.workdir, daemon: daemon(created, runs.live(created.id)) };
    }],
    ...projectRoutes(store, runs, project),
    ['GET', /^\/api\/projects\/([^/]+)\/snapshot$/, ({ params: [id] }) => snapshot(store, project(id!), runs.live(id!))],
    // The record's events as they are appended, each stream reading on from where it stopped. A page
    // that reconnects names the last line it has (Last-Event-ID) and is sent those after it; a new
    // one, the last `replay`.
    ['GET', /^\/api\/projects\/([^/]+)\/stream$/, ({ params: [id], url, request, response }) => {
      const work = project(id!);
      const send = sse(response);
      if (url.searchParams.get('view') === 'rows') {
        connected(send);
        send({ type: 'projects', ...list() });
        send({ type: 'snapshot', id: work.id, snapshot: snapshot(store, work, runs.live(work.id)) });
        watchers.set(send, work.id);
        let previous = new Map<string, string>();
        let cursor = Number(request.headers['last-event-id'] ?? url.searchParams.get('after') ?? -1);
        let roleCursor = -1;
        const wake = listeners.get(id!) ?? new Set();
        listeners.set(id!, wake);
        const flush = (moved = true) => {
          // A command or an edit moves no row, and the project is not read for it; one deleted
          // meanwhile ends its stream.
          let now: Project | undefined;
          if (moved) try { now = store.load(work.id); } catch { wake.delete(flush); response.end(); return; }
          const reading = readRecord(store, work.id);
          const rows = now ? conversationRows(reading, now, runs.live(work.id)) : [];
          // A row goes out when what it shows changed, not the line it was read at; a stream just
          // begun sends those past its cursor.
          const shown = new Map(rows.map(row => [String(row.row_id), JSON.stringify({ ...row, event_id: 0 })]));
          const changed = rows.filter(row => previous.size
            ? previous.get(String(row.row_id)) !== shown.get(String(row.row_id)) : Number(row.event_id) > cursor);
          if (moved) previous = shown;
          cursor = reading.fold.line - 1;
          send({ type: 'ui.projection', event_id: cursor, cursor, rows: changed, round: reading.round,
            role_events: reading.roleEvents.filter(event => Number(event.event_id) > roleCursor) });
          roleCursor = cursor;
        };
        flush();
        wake.add(flush);
        response.on('close', () => { wake.delete(flush); watchers.delete(send); });
        return;
      }
      const read = follow(store, work.id);
      const had = Number(request.headers['last-event-id'] || NaN);
      const replay = Math.min(200, Number(url.searchParams.get('replay') ?? 40));
      const all = read();
      (Number.isInteger(had) ? all.filter(event => Number(event.event_id) > had) : all.slice(Math.max(0, all.length - replay))).forEach(send);
      const flush = (): void => read().forEach(send);
      const wake = listeners.get(id!) ?? new Set();
      listeners.set(id!, wake.add(flush));
      response.on('close', () => wake.delete(flush));
    }],
    // The project list, then each project's state as it changes: the page asks for none of it again.
    ['GET', /^\/api\/projects\/stream$/, ({ response }) => {
      const send = sse(response);
      connected(send);
      send({ type: 'projects', ...list() });
      watchers.set(send, null);
      response.on('close', () => watchers.delete(send));
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/conversation$/, ({ params: [id], url }) => {
      const work = project(id!);
      const reading = readRecord(store, work.id);
      const limit = Number(url.searchParams.get('limit') ?? 30);
      const before = url.searchParams.has('before') ? Number(url.searchParams.get('before')) : Infinity;
      if (!Number.isInteger(limit) || limit < 1 || limit > 120 || Number.isNaN(before)) throw new HttpError(400, 'invalid conversation page');
      return conversationPage(conversationRows(reading, work, runs.live(work.id)), reading.fold.line - 1, limit, before);
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/map$/, ({ params: [id], url }) =>
      progressMap(store, project(id!), Number(url.searchParams.get('after')) || 0, runs.live(id!).calls)],
    ['GET', /^\/api\/projects\/([^/]+)\/map-trace$/, ({ params: [id], url }) => taskTrace(store, project(id!), url.searchParams.get('task') ?? '')],
    ['POST', /^\/api\/projects\/([^/]+)\/message\/stream$/, async context => {
      const reply = await message(context);
      const send = sse(context.response);
      send(await reply(send));
      context.response.end();
    }],
    // A message typed while a reply is under way: answered after it, as any other.
    ['POST', /^\/api\/projects\/([^/]+)\/message\/followup$/, async context => {
      const reply = await message(context);
      return new Promise((resolve, reject) => {
        void reply(frame => {
          if (frame.type === 'phase') resolve({ ok: true, queued: !['replied', 'failed', 'replaced'].includes(String(frame.status)), accepted: true, request_id: frame.request_id, status: frame.status });
        }).then(frame => {
          if (frame.type === 'error') reject(new HttpError(400, String(frame.error), 'message_failed'));
        }, reject);
      });
    }],
    // The operator's draft rewritten by the Manager, for the operator to read before sending it: a
    // turn of its own, which the Manager's conversation knows nothing of. A failed one leaves the
    // draft as it was, and says why.
    ['POST', /^\/api\/projects\/([^/]+)\/prompt\/rewrite$/, async ({ params: [id], body }) => {
      const original = String((await body()).text ?? '').trim();
      if (!original) throw new HttpError(400, 'empty prompt', 'input_required');
      const { id: sid, backend, workdir, model, effort } = project(id!);
      const dir = store.dir(sid);
      const { handoff, error } = await options.callAgent({
        backend, role: 'manager', form: 'manager', access: 'read', cwd: workdir, prompt: rewritePrompt(original),
        ...(model ? { model } : {}), ...(effort ? { effort } : {}), callsDir: join(dir, 'calls'), usageFile: join(dir, 'usage.jsonl'),
      }).catch((thrown: unknown) => ({ handoff: null, error: thrown instanceof Error ? thrown.message : String(thrown) }));
      // Nothing goes on from the letter, whoever it is to: it is the rewrite.
      const rewritten = handoff?.letters.join('\n\n') ?? '';
      return { original, rewritten: rewritten || original, changes: [], questions: [], error: rewritten ? '' : error || 'The Manager wrote no rewrite.',
        ...rewritten ? {} : { error_code: 'rewrite_failed' } };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/message$/, async context => {
      const { domain_answer: card, attachments: files } = await context.body();
      if (card && typeof card === 'object') {
        const { id, note } = card as Record<string, unknown>;
        await answer(context.params[0]!, String(id), String(note ?? ''), files);
        return { kind: 'chat', reply: null, resolved: true };
      }
      const frame = await (await message(context))(() => {});
      return frame.type === 'done' ? frame.result : { kind: 'error', reply: frame.error };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/decisions\/([^/]+)\/resolve$/, async ({ params: [id, card], body }) => {
      const { note, attachments: files } = await body();
      await answer(id!, card!, String(note ?? ''), files);
      return { resolved: true };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/daemon\/start$/, async ({ params: [id], body }) => {
      runs.resume(project(id!).id);
      return command(id!, await body());
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/daemon\/stop$/, async ({ params: [id], body }) => {
      const asked = await body();
      const { id: sid } = project(id!);
      runs.stop(sid);
      return command(sid, asked);
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/artifacts$/, ({ params: [id] }) => ({ artifacts: artifacts(project(id!)) })],
    ['GET', /^\/api\/projects\/([^/]+)\/artifact$/, ({ params: [id], url }) => {
      const work = project(id!);
      const path = url.searchParams.get('path') ?? '';
      const found = result(work, path, url.searchParams.get('delivery'));
      if (!found) throw new HttpError(404, 'no such result');
      return artifactInfo(found, true);
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/raw$/, ({ params: [id], url, request, response }) => {
      const work = project(id!);
      const path = url.searchParams.get('path') ?? '';
      const found = result(work, path, url.searchParams.get('delivery'));
      if (!found) throw new HttpError(404, 'no such result');
      sendFile(request, response, found.file, found.stat.size, rawHeaders(found.file, url.searchParams.get('download') === 'true', found.path));
    }],
    // A file the 3D viewer's model names: a mesh, a texture, a glTF buffer.
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/model-asset$/, ({ params: [id], url, request, response }) => {
      const work = project(id!);
      const ref = (url.searchParams.get('ref') ?? '').replace(/\\/g, '/').split(/[?#]/)[0]!;
      let found: ReturnType<typeof under> = null;
      for (const path of modelAssets(url.searchParams.get('path') ?? '', ref)) {
        if ((found = result(work, path, url.searchParams.get('delivery')))) break;
      }
      if (!found) throw new HttpError(404, `the model's file is not in the workspace: ${ref}`);
      sendFile(request, response, found.file, found.stat.size, rawHeaders(found.file, false, found.path));
    }],
    ...library(store), ...workspace(project), ...sites.routes, ...records(store, runs, project), ...resources(project),
  ];

  const handle = async (request: IncomingMessage, response: ServerResponse): Promise<void> => {
    const incoming = requestOrigin(request);
    if (sites.isHost(incoming.hostname)) return sites.handle(request, response);
    const url = new URL(request.url ?? '/', 'http://127.0.0.1');
    const method = request.method === 'HEAD' ? 'GET' : request.method;
    if (!url.pathname.startsWith('/api/')) {
      if (method === 'GET' && sendStatic(request, response, options.web, decodeURIComponent(url.pathname))) return;
      throw new HttpError(404, 'Not Found');
    }
    // Without a bearer secret, browser access belongs to this API's own origin. A token
    // still authorizes a development proxy rewriting Host, but never the preview origin.
    const origin = request.headers.origin;
    if (origin && (sites.isOrigin(origin)
      || !token && origin !== incoming.origin)) throw new HttpError(403, 'This origin cannot access the cockpit API.');
    if (url.pathname === '/api/meta') {
      response.setHeader('cache-control', 'no-store');
      return json(response, 200, meta(authorized(request)));
    }
    if (!url.pathname.startsWith('/api/application-brand') && !authorized(request)) throw new HttpError(401, 'invalid or missing bearer token');
    for (const [verb, pattern, handler] of routes) {
      const match = pattern.exec(url.pathname);
      if (!match || verb !== method) continue;
      let body: Promise<Record<string, unknown>> | undefined;
      const result = await handler({
        request, response, url, params: match.slice(1).map(decodeURIComponent), body: () => body ??= readBody(request),
      });
      if (!response.headersSent) json(response, 200, result);
      return;
    }
    throw new HttpError(404, 'Not Found');
  };

  const server = createServer((request, response) => {
    handle(request, response).catch((error: unknown) => {
      const status = error instanceof HttpError ? error.status : 500;
      if (status === 500) process.stderr.write(`${request.method} ${request.url}: ${error instanceof Error ? error.stack : String(error)}\n`);
      if (!response.headersSent) json(response, status, { detail: error instanceof Error ? error.message : String(error),
        code: error instanceof HttpError ? error.code || 'request_failed' : 'internal_error' });
      else response.end();
    });
  });
  await new Promise<void>((resolveListen, reject) => {
    server.once('error', reject).listen(options.port, host, resolveListen);
  });
  // Work a shutdown cut off goes on, the Manager's answer to a message too; what the operator stopped
  // stays stopped, a message being answered in it included, as if stopped again; a failure waits for
  // the operator.
  for (const { id, status, forManager } of store.list()) {
    if (status === 'working' || forManager?.length && status !== 'stopped' && status !== 'failed') runs.resume(id);
  }
  return {
    url: `http://${hostPort(host, (server.address() as AddressInfo).port)}`,
    close: async () => {
      await runs.stopAll();
      await sites.close();
      server.closeAllConnections();
      await new Promise(resolveClose => server.close(resolveClose));
    },
  };
}

/** An address and port as a URL writes them: an IPv6 address in brackets. */
export const hostPort = (address: string, port?: number) => `${address.includes(':') ? `[${address}]` : address}:${port}`;

/** The browser-facing protocol, including HTTPS terminated by a reverse proxy. */
export function requestOrigin(request: IncomingMessage): URL {
  const forwarded = request.headers['x-forwarded-proto']?.toString().split(',')[0]?.trim();
  const protocol = forwarded === 'http' || forwarded === 'https' ? forwarded
    : 'encrypted' in request.socket && request.socket.encrypted ? 'https' : 'http';
  // Without a Host, or with an empty one: the connection's own address.
  const host = request.headers.host || hostPort(request.socket.localAddress ?? '', request.socket.localPort);
  if (!/^(?:\[[0-9a-fA-F:.]+\]|[a-zA-Z0-9._-]+)(?::[0-9]+)?$/.test(host)) throw new HttpError(400, 'invalid Host header');
  try { return new URL(`${protocol}://${host}`); }
  catch { throw new HttpError(400, 'invalid Host header'); }
}

/** Whether the secret offered is the one expected, in a time that tells nothing of how much of it is. */
function same(offered: string | undefined, expected: string): boolean {
  const [given, wanted] = [Buffer.from(offered ?? ''), Buffer.from(expected)];
  return given.length === wanted.length && timingSafeEqual(given, wanted);
}

function json(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, { 'content-type': 'application/json' }).end(JSON.stringify(body));
}

async function readBody(request: IncomingMessage): Promise<Record<string, unknown>> {
  request.setEncoding('utf8');
  let text = '';
  for await (const chunk of request) {
    text += chunk;
    if (text.length > BODY_LIMIT) throw new HttpError(413, 'request body too large', 'tooLarge');
  }
  try {
    const body: unknown = text ? JSON.parse(text) : {};
    if (body && typeof body === 'object' && !Array.isArray(body)) return body as Record<string, unknown>;
  } catch { /* answered below */ }
  throw new HttpError(400, 'the request body must be a JSON object', 'invalid_request');
}
