// Read-only observations of the actual packaged WebView. No mock IPC/fetch/model.
export async function cockpitFrame(page, origin) {
  for (const frame of page.frames()) {
    if (frame === page.mainFrame()) continue;
    if (await frame.evaluate(() => location.origin).catch(() => '') === origin) return frame;
  }
  return null;
}

export async function connectionSnapshot(frame) {
  return frame.evaluate(() => {
    const root = document.getElementById('root');
    const key = root && Object.keys(root).find(key => key.startsWith('__reactContainer$'));
    const container = key && root[key];
    const pending = container ? [container.stateNode?.current || container] : [];
    const seen = new Set();
    let client;
    while (pending.length && seen.size < 20000) {
      const fiber = pending.pop();
      if (!fiber || seen.has(fiber)) continue;
      seen.add(fiber);
      const candidate = fiber.memoizedProps?.client;
      if (typeof candidate?.getQueryCache === 'function') { client = candidate; break; }
      pending.push(fiber.child, fiber.sibling);
    }
    return {
      origin: location.origin,
      unreachable: [...document.querySelectorAll('[role=alert]')].some(el => /Argus 本地服务当前不可达|local Argus service is unavailable/i.test(el.textContent || '')),
      pairing: Boolean(document.getElementById('pairing-link')),
      queries: client?.getQueryCache().getAll()
        .filter(q => ['projects', 'project-costs'].includes(q.queryKey[0]))
        .map(q => ({ key: q.queryKey[0], status: q.state.status, fetchStatus: q.state.fetchStatus,
          dataAt: q.state.dataUpdatedAt, errorAt: q.state.errorUpdatedAt,
          error: q.state.error?.name || null, errorPath: q.state.error?.path || null })) || [],
    };
  });
}

export function observeConnection(page, invoke, getOrigin) {
  const timeline = [];
  let stopped = false, busy = false, last = '';
  const network = (request, phase, status) => {
    const url = new URL(request.url());
    if (!['/api/projects', '/api/projects/costs', '/api/meta'].includes(url.pathname)) return;
    timeline.push({ at: Date.now(), phase, path: url.pathname, method: request.method(), ...(status === undefined ? {} : { status }) });
  };
  const onRequest = request => network(request, 'request');
  const onResponse = response => network(response.request(), 'response', response.status());
  const onFailed = request => network(request, 'failed');
  page.on('request', onRequest); page.on('response', onResponse); page.on('requestfailed', onFailed);
  const timer = setInterval(async () => {
    if (busy || stopped) return;
    busy = true;
    try {
      const native = await invoke('get_status');
      const origin = getOrigin();
      const frame = origin && await cockpitFrame(page, origin);
      const view = frame ? await connectionSnapshot(frame) : null;
      const sample = { native: native.state, pid: native.pid, view };
      const serialized = JSON.stringify(sample);
      if (serialized !== last) { timeline.push({ at: Date.now(), ...sample }); last = serialized; }
    } catch { /* Navigation/exit can remove an observed frame. */ }
    finally { busy = false; }
  }, 100);
  return { timeline, stop() { stopped = true; clearInterval(timer); page.off('request', onRequest); page.off('response', onResponse); page.off('requestfailed', onFailed); } };
}
