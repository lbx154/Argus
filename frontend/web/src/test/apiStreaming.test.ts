import { createServer, type IncomingHttpHeaders, type Server, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api';

const nativeFetch = globalThis.fetch;
const encoder = new TextEncoder();
const frame = (value: unknown) => `data: ${JSON.stringify(value)}\n\n`;
type RequestRecord = { method?: string; path: string; headers: IncomingHttpHeaders; body: unknown };
let server: Server | undefined;
let requests: RequestRecord[];

async function serve(handler: (response: ServerResponse, request: RequestRecord) => void) {
  server = createServer((request, response) => {
    let body = '';
    request.setEncoding('utf8');
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => {
      const record = { method: request.method, path: request.url!, headers: request.headers, body: body ? JSON.parse(body) : undefined };
      requests.push(record);
      handler(response, record);
    });
  });
  await new Promise<void>(resolve => server!.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const fetch = vi.fn((path: string, init?: RequestInit) => nativeFetch(new URL(path, origin), init));
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

beforeEach(() => {
  requests = [];
  vi.stubGlobal('window', { location: { search: '' } });
  vi.stubGlobal('localStorage', { getItem: () => 'test-pairing-token' });
});

afterEach(async () => {
  server?.closeAllConnections();
  if (server) await new Promise<void>(resolve => server!.close(() => resolve()));
  server = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it.each([
  ['manager', 'done'], ['manager', 'error'],
])('drains the %s %s response through EOF without cancelling after its terminal frame', async (endpoint, type) => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let awaitingEOF!: () => void;
  const readingTail = new Promise<void>(resolve => { awaitingEOF = resolve; });
  const cancel = vi.fn();
  const result = { kind: 'chat', reply: '完整回答' };
  let sent = false;
  const body = new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
    pull(value) {
      if (!sent) {
        sent = true;
        value.enqueue(encoder.encode(frame({ type, result, error: 'Review unavailable' })));
      } else awaitingEOF();
    },
    cancel,
  }, { highWaterMark: 0 });
  const fetch = vi.fn(async () => new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
  vi.stubGlobal('fetch', fetch);
  const onDone = vi.fn();
  const onError = vi.fn();
  const pending = api.messageStream('s-research', 'Explain this', { onDone, onError });
  const outcome = pending.then(value => ({ value }), error => ({ error }));

  // The terminal frame has been consumed, but HTTP EOF has not arrived yet.
  expect(await Promise.race([readingTail.then(() => 'draining'), outcome.then(() => 'settled')])).toBe('draining');
  expect(cancel).not.toHaveBeenCalled();
  if (endpoint === 'manager') {
    if (type === 'done') expect(onDone).toHaveBeenCalledExactlyOnceWith(result);
    else expect(onError).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ message: 'Review unavailable' }));
  }
  controller.enqueue(encoder.encode(': response complete\n\n'));
  controller.close();
  await expect(outcome).resolves.toEqual({ value: undefined });
  expect(cancel).not.toHaveBeenCalled();
  expect(body.locked).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('preserves ordinary JSON messages and Manager acceptance, delta and done callbacks over HTTP', async () => {
  const result = { kind: 'chat', reply: '完整回答' };
  const reply = { type: 'ui.argus', agent_layer: 'manager', to: 'operator', text: '完整回答', ts: 2, event_id: 3, message_id: 'm1-argus' };
  const fetch = await serve((response, request) => {
    if (request.path.endsWith('/message')) {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(result));
      return;
    }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    response.end(frame({ type: 'phase', label: 'Manager', role: 'manager' })
      + frame({ type: 'delta', event: reply })
      + frame({ type: 'done', result }));
  });
  const options = { attachments: [{ attachment_id: 'test-attachment', relative_path: '.argus/attachments/test-attachment/notes.md', original_name: 'notes.md', stored_name: 'notes.md', mime: 'text/markdown', size_bytes: 8 }], routeOverride: 'task' as const };
  await expect(api.message('s-research', 'Explain this', options)).resolves.toEqual(result);
  const onAccepted = vi.fn();
  const onDelta = vi.fn();
  const onDone = vi.fn();
  const onError = vi.fn();
  await api.messageStream('s-research', 'Explain this', { onAccepted, onDelta, onDone, onError }, options);
  expect(requests.map(request => request.path)).toEqual(['/api/projects/s-research/message', '/api/projects/s-research/message/stream']);
  expect(requests.map(request => request.body)).toEqual([0, 1].map(() => ({ text: 'Explain this', attachments: options.attachments, route_override: 'task' })));
  expect(onAccepted).toHaveBeenCalledExactlyOnceWith();
  expect(onDelta).toHaveBeenCalledExactlyOnceWith(reply);
  expect(onDone).toHaveBeenCalledExactlyOnceWith(result);
  expect(onError).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('keeps Manager terminal errors on the existing callback and does not replay them', async () => {
  const fetch = await serve(response => {
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    response.end(frame({ type: 'error', error: 'Manager unavailable' }));
  });
  const onError = vi.fn();
  const onDone = vi.fn();
  await api.messageStream('s-research', 'Explain this', { onError, onDone });
  expect(onError).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ message: 'Manager unavailable' }));
  expect(onDone).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledTimes(1);
});
