/** Carry streaming HTTP through a Cloudflare Quick Tunnel WebSocket origin. */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/_relay') return new Response('Not found', { status: 404 });
    const origin = new URL(env.ORIGIN);
    if (request.headers.get('Upgrade')?.toLowerCase() === 'websocket') {
      origin.pathname = url.pathname;
      origin.search = url.search;
      const headers = new Headers(request.headers);
      headers.set('X-Argus-Public-Host', url.host);
      headers.set('Authorization', `Bearer ${env.RELAY_SECRET}`);
      return fetch(new Request(origin, { method: request.method, headers }));
    }
    const body = ['GET', 'HEAD'].includes(request.method) ? new Uint8Array() : new Uint8Array(await request.arrayBuffer());
    if (body.byteLength > 16 * 1024 * 1024) return new Response('文件太大。', { status: 413 });
    origin.pathname = '/_relay';
    origin.search = '';
    let response;
    try {
      response = await fetch(origin, { headers: { Upgrade: 'websocket', Authorization: `Bearer ${env.RELAY_SECRET}` } });
    } catch {
      return new Response('试用服务暂时没有连接，请稍后刷新。', { status: 503 });
    }
    const socket = response.webSocket;
    if (!socket) return new Response('试用服务暂时没有连接，请稍后刷新。', { status: 503 });
    socket.accept();
    socket.binaryType = "arraybuffer";
    let encoded = '';
    for (let i = 0; i < body.length; i += 16384) encoded += String.fromCharCode(...body.subarray(i, i + 16384));
    let resolveStart, rejectStart, controller;
    let ended = false;
    let started = false;
    const ready = new Promise((resolve, reject) => { resolveStart = resolve; rejectStart = reject; });
    const timeout = setTimeout(() => fail('Origin timeout'), 30000);
    const stream = new ReadableStream({
      start(value) { controller = value; },
      cancel() { ended = true; socket.close(1000, 'Client disconnected'); clearTimeout(timeout); },
    });
    function fail(reason) {
      clearTimeout(timeout);
      if (ended) return;
      ended = true;
      if (!started) rejectStart(new Error(reason));
      controller.error(new Error(reason));
      socket.close(1011, 'Relay disconnected');
    }
    socket.addEventListener('message', event => {
      if (ended) return;
      try {
        if (typeof event.data === 'string') {
          const message = JSON.parse(event.data);
          if (message.type === 'start' && !started) {
            started = true; clearTimeout(timeout); resolveStart(message);
          }
          if (message.type === 'end') {
            ended = true; clearTimeout(timeout); controller.close(); socket.close(1000, 'Complete');
          }
        } else {
          if (!started || controller.desiredSize < -256) return fail('Relay buffer limit');
          controller.enqueue(new Uint8Array(event.data));
        }
      } catch { fail('Invalid relay response'); }
    });
    socket.addEventListener('close', () => { if (!ended) fail('Origin disconnected'); });
    socket.addEventListener('error', () => fail('Relay error'));
    request.signal.addEventListener('abort', () => fail('Client disconnected'), { once: true });
    socket.send(JSON.stringify({ method: request.method, path: url.pathname + url.search,
      host: url.host, headers: Object.fromEntries(request.headers), body: btoa(encoded) }));
    let start;
    try { start = await ready; } catch { return new Response('试用服务连接中断，请刷新后重试。', { status: 502 }); }
    const headers = new Headers(start.headers);
    headers.delete('content-length');
    headers.set('Cache-Control', 'private, no-store');
    if (request.method === 'HEAD' || [204, 205, 304].includes(start.status)) {
      ended = true; socket.close(); return new Response(null, { status: start.status, headers });
    }
    return new Response(stream, { status: start.status, headers });
  },
};
