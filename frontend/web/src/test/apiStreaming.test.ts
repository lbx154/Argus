import { createServer, type IncomingHttpHeaders, type Server, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import type { MapCopy } from '../map/presentation';
import type { ReaderPreview } from '../map/copyMode';

const nativeFetch = globalThis.fetch;
const encoder = new TextEncoder();
const frame = (value: unknown) => `data: ${JSON.stringify(value)}\n\n`;
const requestBody = { cards: [{ key: 'task', task_id: 'task', kind: 'task', event_ids: ['review'] }], locale: 'zh-CN' };
const complete: MapCopy = {
  version: 21, model_revision: 'review-high', cache_revision: 2, available: true, relations: [],
  cards: { task: { title: '完整讲解', summary: '复核后的结论', detail: '保留条件与证据。', generated_at: 130,
    reader_brief: { why: '研究动机', scope: '结论范围', next: '下一步', concept: null },
    model_revision: 'review-high', event_ids: ['review'],
  } },
};
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
  ['', null], ['?reader_preview=other', null], ['?project=research&reader_preview=source-first', 'true'],
  ['?project=research&reader_preview=learning-path', 'learning-path'],
])('selects the same map-copy cache and generation mode from %s', async (search, preview) => {
  vi.stubGlobal('window', { location: { search } });
  const fetch = await serve((response, request) => {
    if (request.method === 'GET') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(complete));
    } else {
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      response.end(frame({ type: 'heartbeat', quiet_s: 0 }) + frame({ type: 'done', result: complete }));
    }
  });
  const controller = new AbortController();
  await expect(api.mapCopy('project', 'research name', 'zh-CN', controller.signal, 's research')).resolves.toEqual(complete);
  await expect(api.generateMapCopy('project', 'research name', requestBody, controller.signal, 's research')).resolves.toEqual(complete);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(requests.map(request => request.method)).toEqual(['GET', 'POST']);
  expect(requests.map(request => request.body)).toEqual([undefined, requestBody]);
  requests.forEach((request, index) => {
    const url = new URL(request.path, 'http://argus.test');
    expect(url.pathname).toBe('/api/map-copy/project/research%20name');
    expect(url.searchParams.get('session_id')).toBe('s research');
    expect(url.searchParams.get('preview')).toBe(preview);
    expect(url.searchParams.get(index ? 'stream' : 'locale')).toBe(index ? 'true' : 'zh-CN');
    expect(request.headers.authorization).toBe('Bearer test-pairing-token');
  });
});

it.each<ReaderPreview>([null, 'source-first', 'learning-path'])('keeps scheduled GET and POST requests in captured mode %s after the page URL changes', async preview => {
  vi.stubGlobal('window', { location: { search: preview === 'learning-path' ? '?reader_preview=source-first' : '?reader_preview=learning-path' } });
  await serve((response, request) => {
    if (request.method === 'GET') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(complete));
    } else {
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      response.end(frame({ type: 'done', result: complete }));
    }
  });
  await api.mapCopy('project', 'research', 'zh-CN', undefined, 's research', preview);
  await api.generateMapCopy('project', 'research', requestBody, undefined, 's research', preview);
  expect(requests.map(request => request.method)).toEqual(['GET', 'POST']);
  for (const request of requests) expect(new URL(request.path, 'http://argus.test').searchParams.get('preview'))
    .toBe(preview === 'source-first' ? 'true' : preview);
});

it('waits through 125 seconds of HTTP heartbeats and returns only the complete explanation at 130 seconds', async () => {
  // The HTTP connection is real; only the model/heartbeat clock is accelerated.
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
  let start!: () => void;
  const started = new Promise<void>(resolve => { start = resolve; });
  const heartbeats: number[] = [];
  const fetch = await serve(response => {
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    response.write(frame({ type: 'heartbeat', quiet_s: 0 }));
    let seconds = 0;
    const timer = setInterval(() => {
      seconds += 5;
      if (seconds < 130) {
        heartbeats.push(seconds);
        response.write(frame({ type: 'heartbeat', quiet_s: seconds }));
      } else {
        clearInterval(timer);
        response.end(frame({ type: 'done', result: complete }));
      }
    }, 5_000);
    response.on('close', () => clearInterval(timer));
    start();
  });
  const controller = new AbortController();
  const pending = api.generateMapCopy('project', 'research name', requestBody, controller.signal, 's research');
  let settled = false;
  void pending.then(() => { settled = true; });
  await started;
  await fetch.mock.results[0].value;
  await vi.advanceTimersByTimeAsync(125_000);
  expect(settled).toBe(false);
  expect(heartbeats).toHaveLength(25);
  expect(heartbeats.at(-1)).toBe(125);
  await vi.advanceTimersByTimeAsync(5_000);
  await expect(pending).resolves.toEqual(complete);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][1]?.signal).toBe(controller.signal);
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({ method: 'POST', body: requestBody,
    headers: { authorization: 'Bearer test-pairing-token', 'content-type': 'application/json' } });
  const url = new URL(requests[0].path, 'http://argus.test');
  expect(url.pathname).toBe('/api/map-copy/project/research%20name');
  expect(Object.fromEntries(url.searchParams)).toEqual({ stream: 'true', session_id: 's research' });
});

it('reassembles UTF-8, JSON and CRLF frame boundaries split into individual bytes', async () => {
  const bytes = encoder.encode((frame({ type: 'heartbeat' }) + frame({ type: 'done', result: complete })).replaceAll('\n', '\r\n'));
  let offset = 0;
  const fetch = vi.fn(async () => new Response(new ReadableStream<Uint8Array>({
    pull(controller) {
      if (offset < bytes.length) controller.enqueue(bytes.slice(offset, ++offset));
      else controller.close();
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } }));
  vi.stubGlobal('fetch', fetch);
  await expect(api.generateMapCopy('dataset', 'example', requestBody)).resolves.toEqual(complete);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([
  ['map', 'done'], ['map', 'error'], ['manager', 'done'], ['manager', 'error'],
])('drains the %s %s response through EOF without cancelling after its terminal frame', async (endpoint, type) => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  let awaitingEOF!: () => void;
  const readingTail = new Promise<void>(resolve => { awaitingEOF = resolve; });
  const cancel = vi.fn();
  const result = endpoint === 'map' ? complete : { kind: 'chat', reply: '完整回答' };
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
  const pending = endpoint === 'map'
    ? api.generateMapCopy('project', 'research', requestBody)
    : api.messageStream('s-research', 'Explain this', { onDone, onError });
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
  if (endpoint === 'map' && type === 'error') {
    await expect(outcome).resolves.toEqual({ error: expect.objectContaining({ message: 'Review unavailable' }) });
  } else {
    await expect(outcome).resolves.toEqual({ value: endpoint === 'map' ? complete : undefined });
  }
  expect(cancel).not.toHaveBeenCalled();
  expect(body.locked).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([
  ['terminal error', frame({ type: 'error', error: 'Explanation review unavailable', status: 503 }), 'Explanation review unavailable'],
  ['early EOF', frame({ type: 'heartbeat' }), 'ended before a terminal event'],
  ['truncated result', frame({ type: 'heartbeat' }) + 'data: {"type":"done","result":', 'ended before a terminal event'],
])('rejects an HTTP %s without replaying the POST', async (_name, responseBody, message) => {
  const fetch = await serve(response => {
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    response.end(responseBody);
  });
  await expect(api.generateMapCopy('project', 'research', requestBody)).rejects.toThrow(message);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(requests).toHaveLength(1);
});

it('preserves an HTTP rejection before the stream opens without replaying the POST', async () => {
  const fetch = await serve(response => {
    response.writeHead(422, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ detail: 'event does not belong to the task' }));
  });
  await expect(api.generateMapCopy('project', 'research', requestBody)).rejects.toMatchObject({ status: 422 });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('aborts an open HTTP explanation stream without returning a partial card or replaying it', async () => {
  let start!: () => void;
  const started = new Promise<void>(resolve => { start = resolve; });
  const fetch = await serve(response => {
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    response.write(frame({ type: 'heartbeat' }));
    start();
  });
  const controller = new AbortController();
  const pending = api.generateMapCopy('project', 'research', requestBody, controller.signal);
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  await started;
  await fetch.mock.results[0].value;
  controller.abort();
  await rejected;
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('preserves ordinary JSON messages and Manager phase, delta and done callbacks over HTTP', async () => {
  const result = { kind: 'chat', reply: '完整回答' };
  const fetch = await serve((response, request) => {
    if (request.path.endsWith('/message')) {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(result));
      return;
    }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    response.end(frame({ type: 'phase', label: 'Reviewing', role: 'manager', heartbeat: true, quiet_s: 5 })
      + frame({ type: 'delta', text: '完整回答', message_id: 'm1', fragment_mode: 'append' })
      + frame({ type: 'done', result }));
  });
  const options = { attachments: [{ attachment_id: 'test-attachment' }], routeOverride: 'task' as const };
  await expect(api.message('s-research', 'Explain this', options)).resolves.toEqual(result);
  const onPhase = vi.fn();
  const onDelta = vi.fn();
  const onDone = vi.fn();
  const onError = vi.fn();
  await api.messageStream('s-research', 'Explain this', { onPhase, onDelta, onDone, onError }, options);
  expect(requests.map(request => request.path)).toEqual(['/api/projects/s-research/message', '/api/projects/s-research/message/stream']);
  expect(requests.map(request => request.body)).toEqual([0, 1].map(() => ({ text: 'Explain this', attachments: options.attachments, route_override: 'task' })));
  expect(onPhase).toHaveBeenCalledWith('Reviewing', 'manager', expect.objectContaining({ heartbeat: true, quietS: 5 }));
  expect(onDelta).toHaveBeenCalledWith('完整回答', 'm1', 'append');
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
