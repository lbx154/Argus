import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiClient, parseSSEFrames, taskDispatchMessage } from '../src/api.js';
import { messageId, mergeFragment, renderEvent } from '../src/eventRender.js';
import { buildEventLines, partitionEventLines } from '../src/eventLines.js';

test('parseSSEFrames decodes whole frames and keeps the partial tail', () => {
  const { frames, rest } = parseSSEFrames(
    'data: {"type":"phase","label":"Manager · reading"}\n\n' +
      'data: {"type":"delta","text":"你好","message_id":"m1"}\n\n' +
      'data: {"type":"delta","text":"需要', // partial — no terminating blank line
  );
  assert.equal(frames.length, 2);
  assert.equal(frames[0].type, 'phase');
  assert.equal(frames[1].type, 'delta');
  assert.equal(frames[1].text, '你好');
  assert.ok(rest.startsWith('data: {"type":"delta","text":"需要')); // buffered for the next chunk
});

test('parseSSEFrames handles a frame split across two chunks', () => {
  const a = parseSSEFrames('data: {"type":"del');
  assert.equal(a.frames.length, 0);
  const b = parseSSEFrames(a.rest + 'ta","text":"hi","message_id":"m1"}\n\n');
  assert.equal(b.frames.length, 1);
  assert.equal(b.frames[0].text, 'hi');
});

test('parseSSEFrames skips a malformed data line without throwing', () => {
  const { frames } = parseSSEFrames('data: not-json\n\ndata: {"type":"done","result":{"kind":"chat"}}\n\n');
  assert.equal(frames.length, 1);
  assert.equal(frames[0].type, 'done');
});

test('messageId coalesces ui.argus reply blocks by message_id', () => {
  // Same message_id across blocks → they belong to one growing reply row.
  assert.equal(messageId({ type: 'ui.argus', text: 'block one', message_id: 'argus-1' } as never), 'argus-1');
  assert.equal(messageId({ type: 'ui.argus', text: 'x' } as never), '');
  assert.equal(messageId({ type: 'ui.operator', text: 'hi' } as never), '');
});

test('mergeFragment grows a multi-block Manager reply (nothing dropped)', () => {
  let acc = '';
  acc = mergeFragment(acc, 'block one');
  acc = mergeFragment(acc, 'block two');
  assert.equal(acc, 'block one\nblock two');
  // a cumulative resend replaces; a duplicate is skipped
  assert.equal(mergeFragment('你好', '你好,需要帮忙吗'), '你好,需要帮忙吗');
  assert.equal(mergeFragment('full reply here', 'reply'), 'full reply here');
});

test('a message_id stays live only for the active Manager request', () => {
  const lines = buildEventLines([
    { type: 'ui.argus', text: 'partial', message_id: 'reply-1' },
    { type: 'ui.argus', text: 'partial answer', message_id: 'reply-1' },
  ] as never);

  const streaming = partitionEventLines(lines, 'reply-1');
  assert.equal(streaming.committed.length, 0);
  assert.equal(streaming.live?.r.text, 'partial answer');

  const settled = partitionEventLines(lines);
  assert.equal(settled.live, null);
  assert.equal(settled.committed[0]?.r.text, 'partial answer');
});

test('renderEvent surfaces the guardian signals that actually persist to the feed', () => {
  // The daemon drops round.watchdog.* in "signal" mode; render the ones that persist.
  const stall = renderEvent({ type: 'round.stall', text: 'no forward progress 2/3 rounds' } as never);
  assert.equal(stall?.label, 'Watch');
  assert.equal(stall?.tone, 'warn');
  assert.equal(renderEvent({ type: 'round.reviewer_backend_failure', text: 'down' } as never)?.tone, 'err');
  assert.equal(renderEvent({ type: 'life.lifecycle.block', reason: 'needs creds' } as never)?.tone, 'err');
});

test('renderEvent surfaces ANY operator_alert event loud, even an unknown type', () => {
  const r = renderEvent({ type: 'some.new.guardian.signal', operator_alert: true, text: 'look here' } as never);
  assert.equal(r?.label, 'Watch');
  assert.equal(r?.tone, 'err');
  assert.ok(r?.text.includes('look here'));
});

test('parked daemon events explain that state was preserved', () => {
  const rendered = renderEvent({
    type: 'daemon.parked',
    replaced_by: 's-new',
    state_preserved: true,
  });
  assert.equal(rendered?.tone, 'warn');
  assert.match(rendered?.text ?? '', /state saved/);
  assert.match(rendered?.text ?? '', /s-new/);
});

test('provider quota denial is visible in the default feed', () => {
  const rendered = renderEvent({
    type: 'provider.request.denied',
    provider: 'codex',
    reason: 'global Codex daily call cap 300 reached',
  });
  assert.equal(rendered?.tone, 'warn');
  assert.match(rendered?.text ?? '', /codex request blocked/);
  assert.match(rendered?.text ?? '', /daily call cap/);
});

test('renderEvent accepts round and round_index lifecycle schemas', () => {
  assert.equal(
    renderEvent({ type: 'round.start', round: 1 } as never)?.text,
    'round 1',
  );
  assert.equal(
    renderEvent({ type: 'round.started', round_index: 2 } as never)?.text,
    'round 2',
  );
});

test('default feed hides internal actions but keeps settled activity milestones', () => {
  assert.equal(renderEvent({ type: 'engineer.progress', kind: 'reasoning', text: 'private thought' } as never), null);
  assert.equal(renderEvent({ type: 'engineer.progress', kind: 'command_execution', command: 'pytest' } as never), null);
  const milestone = renderEvent({
    type: 'role.activity', role: 'engineer', status: 'done', milestone: true,
    label: 'generated 6 candidate ideas',
  } as never);
  assert.equal(milestone?.text, 'generated 6 candidate ideas');
  assert.equal(milestone?.tone, 'ok');
});

test('default feed hides reviewer protocol JSON and empty phase markers', () => {
  assert.equal(renderEvent({
    type: 'engineer.progress', kind: 'agent_message', agent_layer: 'reviewer',
    text: '{"status":"done","reason":"verified"}',
  } as never), null);
  assert.equal(renderEvent({
    type: 'engineer.progress', kind: 'agent_message', agent_layer: 'reviewer',
    text: 'I am rerunning the tests.',
  } as never)?.text, 'I am rerunning the tests.');
  assert.equal(renderEvent({ type: 'life.phase.started', agent_layer: 'reviewer' } as never), null);
});

test('manager stage decision renders its real target_stage', () => {
  const row = renderEvent({
    type: 'life.manager.stage_decision', action: 'advance',
    current_stage: 'inspect', target_stage: 'implement_cli', reason: 'verified',
  } as never);
  assert.ok(row?.text.includes('advance → implement_cli'));
});

test('protected artifact reads carry the configured bearer token', async () => {
  const originalFetch = globalThis.fetch;
  let authorization = '';
  globalThis.fetch = (async (_input: string | URL | Request, init?: RequestInit) => {
    authorization = new Headers(init?.headers).get('Authorization') ?? '';
    return new Response(JSON.stringify({ artifacts: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-test', token: 'secret' });
    assert.deepEqual(await api.getArtifacts(), []);
    assert.equal(authorization, 'Bearer secret');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Ink API errors include the backend detail instead of only a status code', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(
    JSON.stringify({ detail: 'invalid or missing bearer token' }),
    { status: 401, headers: { 'Content-Type': 'application/json' } },
  )) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-test' });
    await assert.rejects(() => api.getArtifacts(), /401: invalid or missing bearer token/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('task dispatch reports executor admission failures instead of claiming work started', () => {
  assert.equal(
    taskDispatchMessage({
      kind: 'task',
      item: {
        id: 'x', title: 'run benchmark', objective: '', status: 'pending',
        priority: 1, max_cost_usd: 5,
      },
      daemon: { rc: 2, error: 'background executor limit 2 reached' },
    }),
    '→ queued but not running: background executor limit 2 reached',
  );
  assert.equal(
    taskDispatchMessage({
      kind: 'task',
      item: {
        id: 'y', title: 'new experiment', objective: '', status: 'pending',
        priority: 1, max_cost_usd: 5,
      },
      daemon: {
        rc: 2,
        admission_required: true,
        limit: 2,
        active_count: 2,
        running_daemons: [],
      },
    }),
    '→ queued: choose one running session to park before starting new experiment',
  );
});

test('Ink can park a selected daemon and start the queued target', async () => {
  const originalFetch = globalThis.fetch;
  let seenUrl = '';
  let seenBody: Record<string, unknown> = {};
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    seenUrl = String(input);
    seenBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
    return new Response(JSON.stringify({
      rc: 0,
      parked_session: 's-old',
      daemon: { alive: true, pid: 42 },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-new' });
    const result = await api.replaceDaemon('s-old', true);
    assert.equal(result.rc, 0);
    assert.match(seenUrl, /\/api\/projects\/s-new\/daemon\/replace$/);
    assert.match(String(seenBody.command_id), /^[0-9a-f-]{36}$/);
    delete seenBody.command_id;
    assert.deepEqual(seenBody, {
      victim_sid: 's-old',
      resume_continuous: true,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Ink can create a global daemon with auth, name, and objective', async () => {
  const originalFetch = globalThis.fetch;
  let seenUrl = '';
  let seenAuth = '';
  let seenBody: Record<string, unknown> = {};
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    seenUrl = String(input);
    seenAuth = new Headers(init?.headers).get('Authorization') ?? '';
    seenBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
    return new Response(JSON.stringify({
      sid: 's-new12345', rc: 0, spawned: true, objective: seenBody.objective,
      daemon: { alive: true, pid: 42 },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-old', token: 'secret' });
    const created = await api.createDaemon('reproduce benchmark', 'Kernel run', '/work/kernel');
    assert.equal(created.sid, 's-new12345');
    assert.equal(created.spawned, true);
    assert.match(seenUrl, /\/api\/daemons$/);
    assert.equal(seenAuth, 'Bearer secret');
    assert.match(String(seenBody.command_id), /^[0-9a-f-]{36}$/);
    delete seenBody.command_id;
    assert.deepEqual(seenBody, {
      objective: 'reproduce benchmark',
      name: 'Kernel run',
      launch_cwd: '/work/kernel',
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Ink /rename patches the current session name', async () => {
  const originalFetch = globalThis.fetch;
  let seenUrl = '';
  let seenMethod = '';
  let seenAuth = '';
  let seenBody: Record<string, unknown> = {};
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    seenUrl = String(input);
    seenMethod = String(init?.method ?? 'GET');
    seenAuth = new Headers(init?.headers).get('Authorization') ?? '';
    seenBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
    return new Response(JSON.stringify({
      ok: true,
      sid: 's-test',
      name: '勾股定理简证',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch;
  try {
    const api = new ApiClient({
      host: '127.0.0.1',
      port: 8799,
      project: 's-test',
      token: 'secret',
    });
    const result = await api.renameProject('勾股定理简证');
    assert.match(seenUrl, /\/api\/projects\/s-test$/);
    assert.equal(seenMethod, 'PATCH');
    assert.ok(seenAuth);
    assert.deepEqual(seenBody, { name: '勾股定理简证' });
    assert.equal(result.name, '勾股定理简证');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Ink /abort posts a control request instead of a backlog task', async () => {
  const originalFetch = globalThis.fetch;
  let seenUrl = '';
  let seenBody: Record<string, unknown> = {};
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    seenUrl = String(input);
    seenBody = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
    return new Response(JSON.stringify({
      requested: true,
      item_id: 'task-1',
      message: 'Stop requested for running task task-1.',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-test' });
    const result = await api.abortMission('operator used /abort');
    assert.match(seenUrl, /\/api\/projects\/s-test\/mission\/abort$/);
    assert.equal(seenBody.reason, 'operator used /abort');
    assert.equal(result.requested, true);
    assert.equal(result.item_id, 'task-1');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Ink Manager requests forward AbortSignal and suppress frames after cancellation', async () => {
  const originalFetch = globalThis.fetch;
  const seenSignals: Array<AbortSignal | null | undefined> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    seenSignals.push(init?.signal);
    if (String(input).endsWith('/message/stream')) {
      return new Response('data: {"type":"delta","text":"stale","message_id":"old"}\n\n', {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      });
    }
    return new Response(JSON.stringify({ kind: 'chat', reply: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;
  try {
    const api = new ApiClient({ host: '127.0.0.1', port: 8799, project: 's-old' });
    const streamController = new AbortController();
    streamController.abort();
    let deltas = 0;
    await api.messageStream('old request', { onDelta: () => { deltas += 1; } }, streamController.signal);
    assert.equal(deltas, 0);
    assert.strictEqual(seenSignals[0], streamController.signal);

    const messageController = new AbortController();
    assert.equal((await api.message('fallback', messageController.signal)).reply, 'ok');
    assert.strictEqual(seenSignals[1], messageController.signal);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
