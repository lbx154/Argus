import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RELEASE_ID, RELEASE_SOURCE_DIGEST } from '../../../core/src/release.generated';
import {
  API_PROTOCOL,
  REQUIRED_API_CAPABILITIES,
  SNAPSHOT_SCHEMA_VERSION,
} from '../../../core/src/protocol';

const currentMeta = {
  service: 'argus-skill-webapi',
  protocol: { name: API_PROTOCOL.name, major: API_PROTOCOL.major, minor: API_PROTOCOL.minServerMinor },
  snapshot_schema_version: SNAPSHOT_SCHEMA_VERSION,
  capabilities: [...REQUIRED_API_CAPABILITIES],
  runtime: {
    package_version: '0.1.1',
    source_root: '/checkout/argus-skill',
    configured_source_root: '/checkout/argus-skill',
    source_root_matches_config: true,
    revision: 'abc123',
    pid: 12,
    python_version: '3.13.0',
    executable: '/venv/bin/python',
    started_at: '2026-07-11T00:00:00Z',
    release_id: RELEASE_ID,
    manifest_source_digest: RELEASE_SOURCE_DIGEST,
    runtime_source_digest: RELEASE_SOURCE_DIGEST,
    release_matches_source: true,
  },
};

const currentSnapshot = {
  schema_version: SNAPSHOT_SCHEMA_VERSION,
  daemon: {
    global_daily_cap_usd: 0,
    read_status: 'ok',
    read_error: '',
    protocol_compatible: true,
    protocol_error: '',
  },
  spend_usd: 0,
  spend_status: 'ok',
  usage_summary: {},
  request_usage: {},
  cost_control: {},
  daemon_commands: {},
  observability: {},
  mission_view: {},
  partial: false,
  diagnostics: [],
};

describe('web API protocol handshake', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal('window', { location: { search: '' } });
    vi.stubGlobal('localStorage', { getItem: () => null });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('does not reconnect a stream after the server rejects an unknown project', async () => {
    vi.useFakeTimers();
    const sockets: MockWebSocket[] = [];

    class MockWebSocket {
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event: { code: number; reason: string }) => void) | null = null;
      onerror: (() => void) | null = null;

      constructor(readonly url: string) {
        sockets.push(this);
      }

      close() {}
    }

    vi.stubGlobal('window', {
      location: { protocol: 'http:', host: '127.0.0.1:8801', search: '' },
    });
    vi.stubGlobal('WebSocket', MockWebSocket);
    const { openStream } = await import('../api');
    const onClose = vi.fn();
    const close = openStream('s-missing', () => undefined, { onClose });

    expect(sockets).toHaveLength(1);
    sockets[0].onclose?.({ code: 4404, reason: 'unknown project' });
    await vi.advanceTimersByTimeAsync(1_001);

    expect(sockets).toHaveLength(1);
    expect(onClose).toHaveBeenCalledWith({
      code: 4404,
      reason: 'unknown project',
      retryable: false,
    });
    close();
  });

  it('still reconnects a stream after a transient network close', async () => {
    vi.useFakeTimers();
    const sockets: MockWebSocket[] = [];

    class MockWebSocket {
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event: { code: number; reason: string }) => void) | null = null;
      onerror: (() => void) | null = null;

      constructor(readonly url: string) {
        sockets.push(this);
      }

      close() {}
    }

    vi.stubGlobal('window', {
      location: { protocol: 'http:', host: '127.0.0.1:8801', search: '' },
    });
    vi.stubGlobal('WebSocket', MockWebSocket);
    const { openStream } = await import('../api');
    const close = openStream('s-live', () => undefined);

    sockets[0].onclose?.({ code: 1006, reason: '' });
    await vi.advanceTimersByTimeAsync(1_001);

    expect(sockets).toHaveLength(2);
    close();
  });

  it('rejects an old backend before requesting projects', async () => {
    const fetchMock = vi.fn(async () => new Response('not found', { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await expect(api.listProjects()).rejects.toThrow(/does not expose \/api\/meta/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('performs one compatible handshake before project reads', async () => {
    const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
      const body = path === '/api/meta' ? currentMeta : { projects: [] };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await expect(api.listProjects()).resolves.toEqual([]);
    await expect(api.listProjects()).resolves.toEqual([]);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/meta',
      '/api/projects',
      '/api/projects',
    ]);
  });

  it('stops before protected reads when this browser is not paired', async () => {
    const fetchMock = vi.fn(async (_path: string) => Response.json({
      ...currentMeta,
      authentication: { required: true, authenticated: false },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const { api, PairingRequiredError } = await import('../api');

    await expect(api.projectCosts()).rejects.toBeInstanceOf(PairingRequiredError);
    await expect(api.projectCosts()).rejects.toBeInstanceOf(PairingRequiredError);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/meta']);
  });

  it('sends the persisted pairing token with the cost request', async () => {
    vi.stubGlobal('localStorage', { getItem: () => 'fresh-desktop-token' });
    const fetchMock = vi.fn(async (path: string) => Response.json(
      path === '/api/meta'
        ? { ...currentMeta, authentication: { required: true, authenticated: true } }
        : { projects: [], generated_at: 0 },
    ));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await expect(api.projectCosts()).resolves.toMatchObject({ projects: [] });
    expect(fetchMock.mock.calls[1]).toEqual([
      '/api/projects/costs',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer fresh-desktop-token' }),
      }),
    ]);
  });

  it('turns native Failed to fetch into a local-service diagnosis', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }));
    const { api, LocalArgusUnavailableError } = await import('../api');

    await expect(api.projectIndex()).rejects.toMatchObject({
      name: LocalArgusUnavailableError.name,
      message: expect.stringMatching(/local Argus service.*Argus Desktop is running/i),
    });
  });

  it('does not retry or poll after an authentication rejection', async () => {
    const { ApiError } = await import('../../../core/src/http');
    const { projectCostPollInterval, queryRetryPolicy } = await import('../hooks');
    const error = new ApiError('unauthorized', 401, 'GET', '/api/projects/costs');

    expect(queryRetryPolicy(0, error)).toBe(false);
    expect(projectCostPollInterval(error)).toBe(false);
    expect(queryRetryPolicy(0, new Error('transient'))).toBe(true);
  });

  it('times out a stalled handshake and allows a clean retry', async () => {
    vi.useFakeTimers();
    let metaAttempts = 0;
    const fetchMock = vi.fn((path: string, init?: RequestInit): Promise<Response> => {
      if (path === '/api/meta') {
        metaAttempts += 1;
        if (metaAttempts === 1) {
          return new Promise((_resolve, reject) => {
            init?.signal?.addEventListener(
              'abort',
              () => reject(new Error('aborted')),
              { once: true },
            );
          });
        }
        return Promise.resolve(Response.json(currentMeta));
      }
      return Promise.resolve(Response.json({ projects: [] }));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    const stalled = expect(api.projectIndex()).rejects.toThrow(
      /GET \/api\/meta timed out after 8s/,
    );
    await vi.advanceTimersByTimeAsync(8_001);
    await stalled;

    await expect(api.projectIndex()).resolves.toEqual({ projects: [] });
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/meta',
      '/api/meta',
      '/api/projects',
    ]);
  });

  it('times out a stalled compact snapshot and succeeds on retry', async () => {
    vi.useFakeTimers();
    let snapshotAttempts = 0;
    const snapshotPath =
      '/api/projects/s-stalled/snapshot?compact=true&events_limit=1';
    const fetchMock = vi.fn((path: string, init?: RequestInit): Promise<Response> => {
      if (path === '/api/meta') return Promise.resolve(Response.json(currentMeta));
      if (path === snapshotPath) {
        snapshotAttempts += 1;
        if (snapshotAttempts === 1) {
          return new Promise((_resolve, reject) => {
            init?.signal?.addEventListener(
              'abort',
              () => reject(new Error('aborted')),
              { once: true },
            );
          });
        }
        return Promise.resolve(Response.json(currentSnapshot));
      }
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    const stalled = expect(api.snapshot('s-stalled')).rejects.toThrow(
      /compact=true.*timed out after 12s/,
    );
    await vi.advanceTimersByTimeAsync(12_001);
    await stalled;

    await expect(api.snapshot('s-stalled')).resolves.toMatchObject({
      schema_version: SNAPSHOT_SCHEMA_VERSION,
      partial: false,
    });
    expect(snapshotAttempts).toBe(2);
  });

  it('prewarms an active project once instead of on every snapshot poll', async () => {
    const paths: string[] = [];
    vi.stubGlobal('fetch', vi.fn((path: string): Promise<Response> => {
      paths.push(path);
      if (path === '/api/meta') return Promise.resolve(Response.json(currentMeta));
      return Promise.resolve(Response.json(currentSnapshot));
    }));
    const { api } = await import('../api');

    await api.activeSnapshot('s-active');
    await api.activeSnapshot('s-active');

    expect(paths).toEqual([
      '/api/meta',
      '/api/projects/s-active/snapshot?compact=true&events_limit=1&prewarm=true',
      '/api/projects/s-active/snapshot?compact=true&events_limit=1',
    ]);
  });

  it('times out when response headers arrive but the JSON body stalls', async () => {
    vi.useFakeTimers();
    let projectAttempts = 0;
    const fetchMock = vi.fn((path: string, init?: RequestInit): Promise<Response> => {
      if (path === '/api/meta') return Promise.resolve(Response.json(currentMeta));
      projectAttempts += 1;
      if (projectAttempts === 1) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            init?.signal?.addEventListener(
              'abort',
              () => controller.error(new DOMException('aborted', 'AbortError')),
              { once: true },
            );
          },
        });
        return Promise.resolve(new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }));
      }
      return Promise.resolve(Response.json({ projects: [], local_cwd: '' }));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    const stalled = expect(api.projectIndex()).rejects.toThrow(
      /GET \/api\/projects timed out after 12s/,
    );
    await vi.advanceTimersByTimeAsync(12_001);
    await stalled;

    await expect(api.projectIndex()).resolves.toEqual({ projects: [], local_cwd: '' });
    expect(projectAttempts).toBe(2);
  });

  it('allows source drift, warns, and still requests projects', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const driftedMeta = {
      ...currentMeta,
      runtime: {
        ...currentMeta.runtime,
        release_matches_source: false,
        runtime_source_digest: 'deadbeef',
      },
    };
    const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
      const body = path === '/api/meta' ? driftedMeta : { projects: [] };
      return Response.json(body);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await expect(api.listProjects()).resolves.toEqual([]);
    expect(warning).toHaveBeenCalledWith(
      'Argus API compatibility warning: python -m argus_skill.release_tools.build_release',
    );
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/meta',
      '/api/projects',
    ]);
  });

  it('passes cancellation signals to project reads', async () => {
    let receivedSignal: AbortSignal | undefined;
    const fetchMock = vi.fn((_path: string, init?: RequestInit): Promise<Response> => {
      receivedSignal = init?.signal ?? undefined;
      return new Promise((_resolve, reject) => {
        receivedSignal?.addEventListener(
          'abort',
          () => reject(new DOMException('aborted', 'AbortError')),
          { once: true },
        );
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');
    const controller = new AbortController();

    const pending = api.events('s-test', 120, controller.signal);
    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(receivedSignal?.aborted).toBe(true);
  });

  it('posts mission aborts to the mission abort endpoint with the reason body', async () => {
    const reason = 'operator asked to stop';
    const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      expect(path).toBe('/api/projects/s-test/mission/abort');
      expect(init).toMatchObject({
        method: 'POST',
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      });
      expect(JSON.parse(String(init?.body))).toEqual({ reason });
      return Response.json({
        requested: true,
        item_id: 'item-1',
        message: 'Stop requested for running task item-1.',
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await expect(api.abortMission('s-test', reason)).resolves.toEqual({
      requested: true,
      item_id: 'item-1',
      message: 'Stop requested for running task item-1.',
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('retries one malformed HTTP daemon create with the same command id', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const fetchMock = vi.fn(async (_path: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>);
      if (bodies.length === 1) {
        return new Response('Invalid HTTP request received.', { status: 400 });
      }
      return Response.json({
        sid: 's-retried', rc: 0, daemon: { alive: false }, objective: '',
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await expect(api.createDaemon('', 'Retry create', '/workspace/output')).resolves.toMatchObject({
      sid: 's-retried',
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(bodies[0].command_id).toBe(bodies[1].command_id);
    expect(bodies[0].workdir).toBe('/workspace/output');
    expect(bodies[0]).not.toHaveProperty('launch_cwd');
  });

  it('rejects an HTTP-successful daemon creation failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
      rc: 3,
      command_status: 'failed',
      error: 'workdir is unavailable',
    })));
    const { api } = await import('../api');

    await expect(api.createDaemon('', 'Broken', '/missing')).rejects.toThrow(
      'workdir is unavailable',
    );
  });

  it('wires the complete Web administration surface', async () => {
    const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
      if (path === '/api/metrics') return Response.json({ slo: { status: 'healthy' } });
      if (path.startsWith('/api/trash?')) return Response.json({ entries: [], total: 0 });
      if (path.endsWith('/plan')) return Response.json({ steps: [], notes: [], error: '' });
      if (path.endsWith('/skills')) return Response.json({ text: 'skills' });
      return Response.json({ ok: true, rc: 0, sid: 's-restored' });
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await api.metrics();
    await api.trash();
    await api.previewPlan('s-test', 'inspect');
    await api.setConfig('s-test', 'manager_model', 'gpt-5.6-sol');
    await api.setBudgets('s-test', {
      per_mission_cap: '20',
      daily_cap: '60',
      global_daily_cap: '120',
      codex_daily_requests: '400',
      copilot_daily_requests: '800',
      copilot_daily_premium: '300',
    });
    await api.setIdentity('s-test', 'operator');
    await api.resetManager('s-test');
    await api.skills('s-test', 'ls');
    await api.setLaunchCwd('s-test', '/workspace');
    await api.setWorkdir('s-test', '/workspace');
    await api.replaceDaemon('s-test', 's-victim');
    await api.upgradeDaemon('s-test');
    await api.restoreTrash('0:projects_trash/20260712/s-old');

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/metrics',
      '/api/trash?query=&limit=100&offset=0',
      '/api/projects/s-test/plan',
      '/api/projects/s-test/config/set',
      '/api/projects/s-test/config/budget',
      '/api/projects/s-test/identity',
      '/api/projects/s-test/reset',
      '/api/projects/s-test/skills',
      '/api/projects/s-test/launch-cwd',
      '/api/projects/s-test/workdir',
      '/api/projects/s-test/daemon/replace',
      '/api/projects/s-test/daemon/upgrade',
      '/api/trash/0%3Aprojects_trash%2F20260712%2Fs-old/restore',
    ]);
    const replaceBody = JSON.parse(String(fetchMock.mock.calls[10][1]?.body));
    expect(replaceBody).toMatchObject({ victim_sid: 's-victim', resume_continuous: false });
  });

  it('rejects HTTP-successful daemon command failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
      rc: 2,
      command_status: 'rejected',
      error: 'stale command revision',
    })));
    const { api } = await import('../api');

    await expect(api.startDaemon('s-test', 1)).rejects.toThrow(
      'stale command revision',
    );
    await expect(api.stopDaemon('s-test', true, 1)).rejects.toThrow(
      'stale command revision',
    );
    await expect(api.upgradeDaemon('s-test', 1)).rejects.toThrow(
      'stale command revision',
    );
  });

  it('message endpoint returns dispatch ack reply for task results', async () => {
    const taskResult = {
      kind: 'task',
      reply: 'executor started',
      daemon: { rc: 0, pid: 42 },
      daemon_alive: false,
      item: { id: 'item-1', title: 'test task' },
    };
    const fetchMock = vi.fn(async (path: string) => {
      if (path === '/api/meta') return Response.json(currentMeta);
      return Response.json(taskResult);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    const result = await api.message('s-test', 'do something');
    expect(result.reply).toBe('executor started');
    expect(result.kind).toBe('task');
  });

  it('rejects a stream that closes after a delta without a terminal event', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      'data: {"type":"delta","text":"partial"}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )));
    const { api } = await import('../api');
    const onDelta = vi.fn();

    await expect(api.messageStream('s-test', 'do something', { onDelta })).rejects.toThrow(
      'ended before a terminal event',
    );
    expect(onDelta).toHaveBeenCalledWith('partial', '', 'auto');
  });

  it('uploads attachments as multipart and reuses attachment ids in message requests', async () => {
    const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/meta') return Response.json(currentMeta);
      if (path === '/api/projects/s-test/attachments') {
        expect(init?.method).toBe('POST');
        expect(init?.body).toBeInstanceOf(FormData);
        const entries = Array.from((init?.body as FormData).entries());
        expect(entries).toHaveLength(1);
        expect(entries[0][0]).toBe('files');
        expect((entries[0][1] as File).name).toBe('notes.md');
        return Response.json({
          attachments: [{
            attachment_id: 'att-123456789abc',
            relative_path: '.argus/attachments/s-test/att-123456789abc/notes.md',
            original_name: 'notes.md',
            stored_name: 'notes.md',
            mime: 'text/markdown',
            size_bytes: 7,
          }],
          limits: {
            max_count: 5,
            max_bytes_per_file: 10 * 1024 * 1024,
            max_total_bytes: 25 * 1024 * 1024,
          },
        });
      }
      expect(path).toBe('/api/projects/s-test/message');
      expect(JSON.parse(String(init?.body))).toEqual({
        text: 'summarize the note',
        attachments: [{ attachment_id: 'att-123456789abc' }],
      });
      return Response.json({ kind: 'chat', reply: 'ok' });
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');
    const file = new File(['# note\n'], 'notes.md', { type: 'text/markdown' });

    const upload = await api.uploadAttachments('s-test', [file]);
    const result = await api.message('s-test', 'summarize the note', {
      attachments: upload.attachments.map((attachment) => ({
        attachment_id: attachment.attachment_id,
      })),
    });

    expect(upload.attachments[0].attachment_id).toBe('att-123456789abc');
    expect(result.reply).toBe('ok');
  });
});
