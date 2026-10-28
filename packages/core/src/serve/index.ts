// `argus serve`: the web cockpit's API and its built pages, on 127.0.0.1 only. Requests read
// the projects' files as they are; messages, answers, starts and stops go to the Flow runs of
// this process. With a token (ARGUS_WEB_TOKEN) every project request needs it as a bearer.

import { randomUUID, timingSafeEqual } from 'node:crypto';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { fileURLToPath } from 'node:url';
import {
  API_PROTOCOL, API_SERVICE, RELEASE_ID, REQUIRED_API_CAPABILITIES, SNAPSHOT_SCHEMA_VERSION, type ApiMeta,
} from '@argus/contracts';
import type { Project, Store } from '../store.js';
import type { Backend, CallAgent } from '../types.js';
import { attached, attachments } from './attachments.js';
import { drafts } from './drafts.js';
import { artifactInfo, artifacts, rawHeaders, sendFile, sendStatic, under } from './files.js';
import { CAPABILITIES, directory, projectRoutes } from './projects.js';
import { Runs, type Frame } from './runs.js';
import { trash } from './trash.js';
import { daemon, mapInfo, progressMap, projectRow, questionId, readEvents, snapshot, taskTrace, turns, webEvents } from './views.js';

export interface ServeOptions {
  store: Store;
  callAgent: CallAgent;
  backend: Backend;
  model: string | null;
  /** 0 picks a free port. */
  port: number;
  /** The built web cockpit: the package's web/, the files of frontend/web/dist its page reaches. */
  web: string;
  token?: string;
}

export interface Served { url: string; close(): Promise<void> }

const BRAND = { schema: 1, id: 'argus', name: 'Argus', tagline: 'Autonomous Work Lab', terminal_wordmark: 'argus', assets: {} };
const SOURCE_ROOT = fileURLToPath(new URL('../../', import.meta.url));
const STARTED = new Date().toISOString();
const BODY_LIMIT = 1 << 20;

export class HttpError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
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
  const { store, token } = options;
  const listeners = new Map<string, Set<() => void>>();
  const runs = new Runs(store, options.callAgent, id => { for (const wake of listeners.get(id) ?? []) wake(); });

  const project = (id: string): Project => {
    try {
      if (/^p-[0-9a-f]+$/.test(id)) return store.load(id);
    } catch { /* not a project */ }
    throw new HttpError(404, `unknown project: ${id}`);
  };
  const authorized = (request: IncomingMessage): boolean => {
    if (!token) return true;
    const offered = Buffer.from(request.headers.authorization ?? '');
    const expected = Buffer.from(`Bearer ${token}`);
    return offered.length === expected.length && timingSafeEqual(offered, expected);
  };
  const meta = (authenticated: boolean): ApiMeta => ({
    service: API_SERVICE,
    protocol: { name: API_PROTOCOL.name, major: API_PROTOCOL.major, minor: API_PROTOCOL.minServerMinor },
    snapshot_schema_version: SNAPSHOT_SCHEMA_VERSION,
    capabilities: [...REQUIRED_API_CAPABILITIES, 'project.attachments.v1', ...CAPABILITIES],
    runtime: {
      package_version: RELEASE_ID.split('+')[0]!, release_id: RELEASE_ID, pid: process.pid, started_at: STARTED,
      source_root: authenticated ? SOURCE_ROOT : '<redacted>', executable: authenticated ? process.execPath : '<redacted>',
      state_root: authenticated ? store.home : null, configured_source_root: null, source_root_matches_config: null,
      revision: null, python_version: '', manifest_source_digest: null, runtime_source_digest: null, release_matches_source: null,
    },
    authentication: { required: Boolean(token), authenticated },
  });
  /** Server-sent events; a frame for a reader who left is dropped. */
  const sse = (response: ServerResponse): ((frame: unknown) => void) => {
    response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', 'x-accel-buffering': 'no' }).flushHeaders();
    return frame => { if (!response.destroyed) response.write(`data: ${JSON.stringify(frame)}\n\n`); };
  };
  /** The operator's answer to the open question, in their own words. */
  const answer = (id: string, card: string, note: string): void => {
    const { question } = project(id);
    if (!question || questionId(question) !== card) throw new HttpError(409, 'This question is no longer open.');
    if (!note.trim()) throw new HttpError(400, 'empty answer');
    runs.answer(id, note.trim());
  };
  /** The message a request carries, checked before any reply is under way. */
  const message = async ({ params: [id], body }: Context): Promise<(send: (frame: Frame) => void) => Promise<Frame>> => {
    const { text, request_id: request, attachments: files } = await body();
    if (typeof text !== 'string' || !text.trim()) throw new HttpError(400, 'empty message');
    const work = project(id!);
    const said = text + attached(work, files);
    return send => runs.message(work.id, said, typeof request === 'string' && request ? request : randomUUID(), send);
  };
  const command = (id: string, body: Record<string, unknown>) =>
    ({ rc: 0, command_id: body.command_id ?? '', command_status: 'applied', daemon: daemon(project(id), runs.live(id)) });

  const routes: [string, RegExp, Handler][] = [
    ...drafts(store, project), ...trash(store, runs, project), ...attachments(project),
    ['GET', /^\/api\/application-brand$/, () => BRAND],
    // And for the saved maps it may open beside a project's.
    ['GET', /^\/api\/map-datasets$/, () => ({ datasets: [] })],
    ['GET', /^\/api\/projects$/, () => ({
      projects: store.list().map(project => projectRow(store, project, runs.live(project.id))).sort((a, b) => b.last_active - a.last_active),
      local_cwd: process.cwd(),
    })],
    ['POST', /^\/api\/daemons$/, async ({ body }) => {
      const workdir = directory(String((await body()).workdir ?? ''));
      const created = store.create({ workdir, request: '', backend: options.backend, model: options.model });
      return { sid: created.id, rc: 0, command_status: 'applied', objective: '', workdir: created.workdir, daemon: daemon(created, runs.live(created.id)) };
    }],
    ...projectRoutes(store, runs, project),
    ['GET', /^\/api\/projects\/([^/]+)\/snapshot$/, ({ params: [id] }) => snapshot(store, project(id!), runs.live(id!))],
    ['GET', /^\/api\/projects\/([^/]+)\/events$/, ({ params: [id], url }) =>
      ({ events: webEvents(readEvents(store, project(id!).id)).slice(-Number(url.searchParams.get('limit') ?? 120)) })],
    ['GET', /^\/api\/projects\/([^/]+)\/stream$/, ({ params: [id], url, response }) => {
      project(id!);
      const send = sse(response);
      const replay = Math.min(200, Number(url.searchParams.get('replay') ?? 40));
      let sent: number | null = null;
      const flush = (): void => {
        const all = webEvents(readEvents(store, id!));
        all.slice(sent ?? Math.max(0, all.length - replay)).forEach(send);
        sent = all.length;
      };
      flush();
      const wake = listeners.get(id!) ?? new Set();
      listeners.set(id!, wake.add(flush));
      response.on('close', () => wake.delete(flush));
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/transcript$/, ({ params: [id], url }) =>
      ({ turns: turns(webEvents(readEvents(store, project(id!).id))).slice(-Number(url.searchParams.get('n') ?? 30)) })],
    ['GET', /^\/api\/projects\/([^/]+)\/map-info$/, ({ params: [id] }) => mapInfo(store, project(id!))],
    // The page asks for the whole history or for the current part of it: either is the whole map.
    ['GET', /^\/api\/projects\/([^/]+)\/map(?:-history)?$/, ({ params: [id], url }) =>
      progressMap(store, project(id!), Number(url.searchParams.get('after')) || 0)],
    ['GET', /^\/api\/projects\/([^/]+)\/map-trace$/, ({ params: [id], url }) => taskTrace(store, project(id!), url.searchParams.get('task') ?? '')],
    ['POST', /^\/api\/projects\/([^/]+)\/message\/stream$/, async context => {
      const reply = await message(context);
      const send = sse(context.response);
      send(await reply(send));
      context.response.end();
    }],
    // A message typed while a reply is under way: answered after it, as any other.
    ['POST', /^\/api\/projects\/([^/]+)\/message\/followup$/, async context => {
      void (await message(context))(() => {});
      return { ok: true, queued: true };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/message\/cancel$/, async ({ params: [id], body }) => {
      const requested = runs.cancel(project(id!).id, String((await body()).request_id ?? ''));
      return { requested, status: requested ? 'cancelled' : 'finished' };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/message$/, async context => {
      const { domain_answer: card } = await context.body();
      if (card && typeof card === 'object') {
        const { id, note } = card as Record<string, unknown>;
        answer(context.params[0]!, String(id), String(note ?? ''));
        return { kind: 'chat', reply: null, resolved: true };
      }
      const frame = await (await message(context))(() => {});
      return frame.type === 'done' ? frame.result : { kind: 'error', reply: frame.error };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/decisions\/([^/]+)\/resolve$/, async ({ params: [id, card], body }) => {
      const { note } = await body();
      answer(id!, card!, String(note ?? ''));
      return { resolved: true };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/daemon\/start$/, async ({ params: [id], body }) => {
      runs.resume(project(id!).id);
      return command(id!, await body());
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/daemon\/stop$/, async ({ params: [id], body }) => {
      const asked = await body();
      if (asked.after_current || asked.drain) {
        return { rc: 1, command_status: 'rejected', error: 'Stopping after the current step is not available; stop now instead.' };
      }
      runs.stop(project(id!).id);
      return command(id!, asked);
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/artifacts$/, ({ params: [id] }) => ({ artifacts: artifacts(project(id!)) })],
    ['GET', /^\/api\/projects\/([^/]+)\/artifact$/, ({ params: [id], url }) => {
      const work = project(id!);
      const found = under(work.workdir, url.searchParams.get('path') ?? '');
      if (!found) throw new HttpError(404, 'no such result');
      return artifactInfo(work, found, true);
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/raw$/, ({ params: [id], url, request, response }) => {
      const found = under(project(id!).workdir, url.searchParams.get('path') ?? '');
      if (!found) throw new HttpError(404, 'no such result');
      sendFile(request, response, found.file, found.stat.size, rawHeaders(found.path, url.searchParams.get('download') === 'true'));
    }],
  ];

  const handle = async (request: IncomingMessage, response: ServerResponse): Promise<void> => {
    response.setHeader('x-argus-release', RELEASE_ID);
    const url = new URL(request.url ?? '/', 'http://127.0.0.1');
    const method = request.method === 'HEAD' ? 'GET' : request.method;
    if (!url.pathname.startsWith('/api/')) {
      if (method === 'GET' && sendStatic(request, response, options.web, decodeURIComponent(url.pathname))) return;
      throw new HttpError(404, 'Not Found');
    }
    if (url.pathname === '/api/meta') {
      response.setHeader('cache-control', 'no-store');
      return json(response, 200, meta(authorized(request)));
    }
    if (url.pathname !== '/api/application-brand' && !authorized(request)) throw new HttpError(401, 'invalid or missing bearer token');
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
      if (!response.headersSent) json(response, status, { detail: error instanceof Error ? error.message : String(error) });
      else response.end();
    });
  });
  await new Promise<void>((resolveListen, reject) => {
    server.once('error', reject).listen(options.port, '127.0.0.1', resolveListen);
  });
  return {
    url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    close: async () => {
      runs.stopAll();
      server.closeAllConnections();
      await new Promise(resolveClose => server.close(resolveClose));
    },
  };
}

function json(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, { 'content-type': 'application/json' }).end(JSON.stringify(body));
}

async function readBody(request: IncomingMessage): Promise<Record<string, unknown>> {
  let text = '';
  for await (const chunk of request) {
    text += String(chunk);
    if (text.length > BODY_LIMIT) throw new HttpError(413, 'request body too large');
  }
  try {
    const body: unknown = text ? JSON.parse(text) : {};
    if (body && typeof body === 'object' && !Array.isArray(body)) return body as Record<string, unknown>;
  } catch { /* answered below */ }
  throw new HttpError(400, 'the request body must be a JSON object');
}
