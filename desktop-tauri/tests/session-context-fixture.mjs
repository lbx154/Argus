import { createServer } from 'node:http';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { resolve, relative, extname } from 'node:path';
import { tmpdir } from 'node:os';
import { knowledgeFixture } from './knowledge-fixture.mjs';

export const FIXTURE_AUTH = 'synthetic-session-fixture';
export const REPORT_PATH = 'results/report.md';
export const REPORTS = Object.freeze({ 's-A': '# A ONLY\n\nSynthetic report A.\n', 's-B': '# B ONLY\n\nSynthetic report B.\n' });
export function deferred() {
  let resolvePromise;
  const promise = new Promise(resolve => { resolvePromise = resolve; });
  return { promise, resolve: resolvePromise };
}

/** Deterministic HTTP replacement, NOT an Argus/provider process. Even a GET
 * snapshot?prewarm=true only reads these synthetic objects. No CLI discovery,
 * real settings, outbound calls, source updates, daemon or task starts exist. */
export async function startSessionFixture(options = {}) {
  const checkout = resolve(import.meta.dirname, '../..');
  const parent = options.dataRoot || process.env.ARGUS_SKILL_HOME || tmpdir();
  await mkdir(parent, { recursive: true });
  const root = await mkdtemp(resolve(parent, 'session-context-'));
  const dist = options.dist || process.env.ARGUS_SESSION_WEB_DIST || resolve(checkout, 'frontend/web/dist');
  const manifestPath = process.env.ARGUS_SESSION_WEB_DIST
    ? resolve(dist, 'fixture-release.json') : resolve(checkout, 'argus/release_manifest.json');
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  const protocol = await readFile(resolve(checkout, 'frontend/core/src/protocol.ts'), 'utf8');
  const capabilities = [...protocol.split('REQUIRED_API_CAPABILITIES = [')[1].split('] as const')[0].matchAll(/'([^']+)'/g)].map(match => match[1]);
  const schema = Number(protocol.match(/SNAPSHOT_SCHEMA_VERSION = (\d+)/)[1]);
  const minor = Number(protocol.match(/minServerMinor: (\d+)/)[1]);
  const rows = [];
  const snapshots = new Map();
  const files = new Map();
  for (const [sid, content] of Object.entries(REPORTS)) {
    const workspace = resolve(root, 'workspaces', sid);
    await mkdir(resolve(workspace, 'results'), { recursive: true });
    await mkdir(resolve(root, 'data/projects', sid), { recursive: true });
    const path = resolve(workspace, REPORT_PATH);
    await writeFile(path, content, { flag: 'wx' });
    const session = { id: sid, display_name: `Project ${sid.slice(2)}`, objective: 'Synthetic session context test; no model calls', last_active: 1, cwd: workspace, workdir: workspace, launch_cwd: workspace };
    await writeFile(resolve(root, 'data/projects', sid, 'session.json'), JSON.stringify(session), { flag: 'wx' });
    rows.push({ ...session, label: session.display_name, daemon_alive: false, daemon_pid: null, uptime_seconds: null });
    snapshots.set(sid, { schema_version: schema, session,
      daemon: { alive: false, pid: null, uptime_seconds: null, backend: 'memory', global_daily_cap_usd: null,
        read_status: 'ok', read_error: null, protocol_compatible: true, protocol_error: null },
      roles: [], backlog: [], recent_events: [], pending_questions: [], manager_requests: [],
      spend_usd: 0, spend_status: 'empty', usage_summary: {}, request_usage: null, cost_control: null, daemon_commands: null,
      observability: null, mission_view: null, partial: false, diagnostics: [], continuous: { enabled: false, objective: '' },
    });
    files.set(sid, { path: REPORT_PATH, storage_path: path, name: 'report.md', why: 'Synthetic fixture, not review evidence', exists: true, kind: 'markdown', mime: 'text/markdown', size: Buffer.byteLength(content), mtime: 1, source: 'delivery' });
  }
  const state = {
    rows, snapshots, files, trace: [], unexpected: [],
    deleted: new Set(), forbidden: new Set(), missing: new Set(), unlisted: new Set(),
    delays: new Map(), allowWrites: new Set(), rewriteResults: new Map(), streams: new Map(),
    streamMode: 'hold',
  };
  const json = (response, value, status = 200) => {
    response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify(value));
  };
  let origin;
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, origin);
      const method = request.method || 'GET';
      const project = url.pathname.match(/^\/api\/projects\/([^/]+)(\/.*)?$/);
      const sid = project ? decodeURIComponent(project[1]) : undefined;
      const endpoint = project?.[2] || '';
      if (!url.pathname.startsWith('/api/')) {
        if (method !== 'GET') return json(response, { error: 'Fixture is read-only' }, 405);
        const path = url.pathname === '/' ? '/index.html' : decodeURIComponent(url.pathname);
        // Only serve built Web files, never arbitrary repository files.
        const target = resolve(dist, `.${path}`);
        if (relative(dist, target).startsWith('..') || !/^\/(?:index\.html|assets\/[^/]+|[^/]+\.(?:svg|png|ico|webmanifest))$/.test(path)) return json(response, { error: 'Not a built asset' }, 404);
        const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.png': 'image/png', '.ico': 'image/x-icon', '.webmanifest': 'application/manifest+json' }[extname(target)] || 'application/octet-stream';
        response.writeHead(200, { 'Content-Type': mime, 'Cache-Control': 'no-store' });
        response.end(await readFile(target));
        return;
      }
      const trace = { method, path: url.pathname + url.search, sid, endpoint };
      state.trace.push(trace);
      if (request.headers.authorization !== `Bearer ${FIXTURE_AUTH}`) return json(response, { error: 'Synthetic pairing required' }, 401);
      if (method !== 'GET' && (method !== 'POST' || !state.allowWrites.has(`${sid}:${endpoint}`))) {
        state.unexpected.push(trace);
        return json(response, { error: 'Write blocked: outside the synthetic test allowlist' }, 403);
      }
      if (state.delays.has(`${sid}:${endpoint}`)) await state.delays.get(`${sid}:${endpoint}`);
      if (response.destroyed) return;
      if (url.pathname === '/api/meta') return json(response, {
        service: 'argus-skill-webapi', protocol: { name: 'argus.webapi', major: 1, minor }, snapshot_schema_version: schema, capabilities,
        authentication: { required: true, authenticated: true },
        runtime: { package_version: manifest.package_version, source_root: checkout, configured_source_root: checkout, source_root_matches_config: true,
          revision: 'synthetic-fixture', pid: process.pid, python_version: 'not-used', executable: 'deterministic-node-fixture', started_at: '2026-09-17T00:00:00Z',
          release_id: manifest.release_id, manifest_source_digest: manifest.source_digest, runtime_source_digest: null, release_matches_source: null },
      });
      if (method === 'GET' && url.pathname === '/api/projects') return json(response, { projects: rows.filter(row => !state.deleted.has(row.id)), local_cwd: root });
      if (method === 'GET' && url.pathname === '/api/projects/costs') return json(response, { projects: [], generated_at: 1 });
      if (method === 'GET' && url.pathname === '/api/system/resources') return json(response, { schema_version: 1, state: 'ready', cpu: {}, memory: {}, disk: {} });
      if (method === 'GET' && url.pathname === '/api/map-datasets') return json(response, { datasets: [] });
      if (method === 'GET' && url.pathname === '/api/skill-library') return json(response, { scopes: [], items: [], verticals: [], active_vertical: '', errors: [] });
      if (method === 'GET' && url.pathname === '/api/plugins') return json(response, { plugins: [] });
      if (method === 'GET' && /^\/api\/map-copy\/dataset\/s-[AB]$/.test(url.pathname)) return json(response, { cards: {}, relations: [], available: false });
      if (method === 'GET') {
        const extra = knowledgeFixture(url) || await options.read?.({ url, state, root });
        if (extra) return json(response, extra.body, extra.status ?? 200);
      }
      if (!project) {
        state.unexpected.push(trace);
        return json(response, { error: 'Read blocked: outside the synthetic test allowlist' }, 403);
      }
      if (!snapshots.has(sid) || state.deleted.has(sid)) return json(response, { error: 'Synthetic session deleted' }, 404);
      if (state.forbidden.has(sid)) return json(response, { error: 'Synthetic session permission denied' }, 403);
      if (method === 'GET') {
        if (endpoint === '/snapshot') return json(response, snapshots.get(sid));
        if (endpoint === '/artifacts') return json(response, { artifacts: state.missing.has(sid) || state.unlisted.has(sid) ? [] : [files.get(sid)] });
        if (endpoint === '/artifact') {
          if (url.searchParams.get('path') !== REPORT_PATH || state.missing.has(sid)) return json(response, { error: 'Synthetic artifact unavailable' }, 404);
          // Bind each response to its pre-created file, not a universal A response.
          return json(response, { ...files.get(sid), preview: await readFile(files.get(sid).storage_path, 'utf8') });
        }
        if (endpoint === '/transcript') return json(response, { turns: [] });
        if (endpoint === '/events') return json(response, { events: [] });
        if (endpoint === '/config') return json(response, { schema_version: 1, trial_mode: false, generated_at_utc: '2026-09-17T00:00:00Z', roles: [], operator_knobs: [], how_to_change: [] });
        if (endpoint === '/wiki') return json(response, { exists: false });
        if (endpoint === '/map-notes') return json(response, { notes: [] });
        if (endpoint === '/map-info') return json(response, { task_count: 0, event_bytes: 0, requires_choice: false, current_task_id: null, current_task_ts: 0, current_event_ts: 0 });
        if (endpoint === '/map-history' || endpoint === '/map') return json(response, { id: sid, title: `Synthetic ${sid}`, kind: 'synthetic', description: 'No provider or model activity', read_only: false, tasks: [], events: [], history_loading: false });
        if (endpoint === '/research-foundations') return json(response, { foundations: [] });
      } else if (method === 'POST' && state.allowWrites.has(`${sid}:${endpoint}`)) {
        const chunks = [];
        let size = 0;
        for await (const chunk of request) {
          size += chunk.length;
          if (size > 1024 * 1024) return json(response, { error: 'Fixture body limit' }, 413);
          chunks.push(chunk);
        }
        const body = Buffer.concat(chunks);
        if (endpoint === '/attachments') {
          const form = await new Request(`${origin}${url.pathname}`, { method: 'POST', headers: request.headers, body }).formData();
          const incoming = form.getAll('files');
          trace.files = await Promise.all(incoming.map(async file => ({ name: file.name, text: await file.text() })));
          return json(response, { attachments: incoming.map((file, index) => ({ attachment_id: `${sid}-file-${index}`, original_name: file.name, stored_name: file.name, relative_path: file.name, mime: file.type, size_bytes: file.size })), limits: { max_count: 8, max_bytes_per_file: 1048576, max_total_bytes: 8388608 } });
        }
        trace.body = JSON.parse(body.toString() || '{}');
        if (endpoint === '/prompt/rewrite') return json(response, state.rewriteResults.get(sid) || { original: trace.body.text, rewritten: `${sid} synthetic rewrite`, changes: [], questions: [], error: '' });
        if (endpoint === '/message/cancel') {
          snapshots.get(sid).manager_requests = [];
          return json(response, { requested: true, status: 'cancelled' });
        }
        if (endpoint === '/message/stream') {
          response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
          if (state.streamMode === 'error') return response.end('data: {"type":"error","error":"Synthetic disconnect after acceptance"}\n\n');
          if (state.streamMode === 'done') return response.end('data: {"type":"done","result":{"kind":"chat","reply":"Synthetic reply"}}\n\n');
          response.write('data: {"type":"phase","label":"Synthetic waiting","role":"manager"}\n\n');
          state.streams.set(sid, response);
          response.on('close', () => state.streams.delete(sid));
          return;
        }
      }
      state.unexpected.push(trace);
      return json(response, { error: 'Request blocked: outside the synthetic test allowlist' }, 403);
    } catch (error) {
      if (!response.headersSent) json(response, { error: `Fixture failure: ${error.message}` }, 500);
      else response.destroy();
    }
  });
  server.on('upgrade', (request, socket) => {
    // No provider/event producer is running. Record the synthetic subscription
    // without proxying any WebSocket to another process.
    const url = new URL(request.url, origin);
    state.trace.push({ method: 'WS', path: url.pathname });
    socket.end('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n');
  });
  await new Promise((yes, no) => { server.once('error', no); server.listen(0, '127.0.0.1', yes); });
  origin = `http://127.0.0.1:${server.address().port}`;
  return { root, origin, state, url: sid => `${origin}/?project=${sid}&view=activity&token=${FIXTURE_AUTH}`,
    async close() {
      for (const stream of state.streams.values()) stream.end();
      server.closeAllConnections();
      await new Promise(resolveClose => server.close(resolveClose));
      await writeFile(resolve(root, 'request-trace.json'), JSON.stringify(state.trace, null, 2), { flag: 'wx' });
    },
  };
}
