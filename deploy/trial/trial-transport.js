/* Stream Manager replies over the trial's authenticated browser WebSocket. */
(() => {
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, options) => {
    const request = new Request(input, options);
    const url = new URL(request.url);
    if (url.origin !== location.origin || request.method !== 'POST' || !/^\/api\/projects\/[a-zA-Z0-9_-]+\/message\/stream$/.test(url.pathname)) {
      return originalFetch(request);
    }
    const body = new Uint8Array(await request.arrayBuffer());
    if (body.length > 2000000) return new Response(JSON.stringify({ detail: '输入内容太长，请缩短后重试。' }), { status: 413 });
    let encoded = '';
    for (let i = 0; i < body.length; i += 16384) encoded += String.fromCharCode(...body.subarray(i, i + 16384));
    const endpoint = new URL('/trial/stream', location.href);
    endpoint.protocol = 'wss:';
    const socket = new WebSocket(endpoint);
    socket.binaryType = 'arraybuffer';
    let controller, resolveStart, rejectStart;
    let ended = false, started = false;
    const ready = new Promise((resolve, reject) => { resolveStart = resolve; rejectStart = reject; });
    const stream = new ReadableStream({
      start(value) { controller = value; },
      cancel() { ended = true; clearTimeout(timer); socket.close(); },
    });
    function fail() {
      if (ended) return;
      ended = true; clearTimeout(timer);
      const error = new Error('连接已中断，请重试。');
      if (!started) rejectStart(error);
      controller.error(error);
      socket.close();
    }
    const timer = setTimeout(fail, 30000);
    socket.onopen = () => socket.send(JSON.stringify({ method: 'POST', path: url.pathname, body: btoa(encoded) }));
    socket.onmessage = event => {
      if (ended) return;
      try {
        if (typeof event.data === 'string') {
          const message = JSON.parse(event.data);
          if (message.type === 'start') { started = true; clearTimeout(timer); resolveStart(message); }
          if (message.type === 'end') { ended = true; controller.close(); socket.close(); }
        } else {
          if (controller.desiredSize < -256) return fail();
          controller.enqueue(new Uint8Array(event.data));
        }
      } catch { fail(); }
    };
    socket.onerror = fail;
    socket.onclose = () => { if (!ended) fail(); };
    request.signal.addEventListener('abort', fail, { once: true });
    if (request.signal.aborted) fail();
    const start = await ready;
    return new Response(stream, { status: start.status, headers: start.headers });
  };
})();
