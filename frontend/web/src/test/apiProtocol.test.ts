import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  API_PROTOCOL,
  REQUIRED_API_CAPABILITIES,
  SNAPSHOT_SCHEMA_VERSION,
} from '../../../core/src/protocol';
import { RELEASE_ID } from '../../../core/src/protocol';

const SOURCE_SUMMARY = '2 files, 10 bytes';

const currentMeta = {
  service: 'argus-skill-webapi',
  protocol: { name: API_PROTOCOL.name, major: API_PROTOCOL.major, minor: API_PROTOCOL.minServerMinor },
  snapshot_schema_version: SNAPSHOT_SCHEMA_VERSION,
  capabilities: [...REQUIRED_API_CAPABILITIES],
  runtime: {
    package_version: RELEASE_ID.split('+')[0],
    release_id: RELEASE_ID,
    manifest_source_digest: SOURCE_SUMMARY,
    runtime_source_digest: SOURCE_SUMMARY,
    release_matches_source: true,
    source_root: '/checkout/argus',
    configured_source_root: '/checkout/argus',
    source_root_matches_config: true,
    revision: 'abc123',
    pid: 12,
    python_version: '3.13.0',
    executable: '/venv/bin/python',
    started_at: '2026-07-11T00:00:00Z',
  },
};

const currentSnapshot = {
  schema_version: SNAPSHOT_SCHEMA_VERSION,
  daemon: {
    read_status: 'ok',
    read_error: '',
    protocol_compatible: true,
    protocol_error: '',
  },
  spend_usd: 0,
  spend_status: 'ok',
  usage_summary: {},
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
    const fetchMock = vi.fn(async () => new Response('unknown project', { status: 404, statusText: 'Not Found' }));
    vi.stubGlobal('fetch', fetchMock);
    const { openStream } = await import('../api');
    const onClose = vi.fn();
    const close = openStream('s-missing', () => undefined, { onClose });

    await vi.advanceTimersByTimeAsync(1_001);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith({ code: 404, reason: 'Not Found', retryable: false });
    close();
  });

  it('delivers each event frame and reconnects after the connection ends, from the last event it had', async () => {
    vi.useFakeTimers();
    const body = 'id: 7\ndata: {"type":"ui.argus","text":"hi","event_id":7}\n\n: comment\n\nid: 8\ndata: {"type":"life.planner.task_added","item_id":"t1","event_id":8}\n\n';
    const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) => new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
    vi.stubGlobal('fetch', fetchMock);
    const { openStream } = await import('../api');
    const events: unknown[] = [];
    const onOpen = vi.fn();
    const onClose = vi.fn();
    const close = openStream('s-live', (event) => events.push(event), { onOpen, onClose });

    await vi.advanceTimersByTimeAsync(0);
    expect(events).toEqual([{ type: 'ui.argus', text: 'hi', event_id: 7 }, { type: 'life.planner.task_added', item_id: 't1', event_id: 8 }]);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith(expect.objectContaining({ code: 0, retryable: true }));
    await vi.advanceTimersByTimeAsync(1_001);
    expect(fetchMock.mock.calls.map(([, init]) => init?.headers)).toEqual([{}, { 'Last-Event-ID': '8' }]);
    close();
  });

  it('still reconnects a stream after a transient network failure', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => { throw new TypeError('network down'); });
    vi.stubGlobal('fetch', fetchMock);
    const { openStream } = await import('../api');
    const close = openStream('s-live', () => undefined);

    await vi.advanceTimersByTimeAsync(1_001);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    close();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('keeps the paired token out of stream URLs and sends it as the bearer header', async () => {
    const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) => new Response('', { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('localStorage', { getItem: () => 'fake-token' });
    const { openStream } = await import('../api');
    openStream('s-live', () => undefined)();
    openStream('s-live', () => undefined, { replay: 40 })();

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/projects/s-live/stream?view=ui',
      '/api/projects/s-live/stream?replay=40&view=ui',
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.headers).toEqual({ Authorization: 'Bearer fake-token' });
    }
  });

  it('rejects an old backend before requesting projects', async () => {
    const fetchMock = vi.fn(async () => new Response('not found', { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await expect(api.listProjects()).rejects.toThrow(/does not expose \/api\/meta/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('rejects a mismatched release before any protected project read', async () => {
    const fetchMock = vi.fn(async () => Response.json({ ...currentMeta,
      runtime: { ...currentMeta.runtime, release_id: 'another-build' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');
    await expect(api.listProjects()).rejects.toThrow(/out of sync/);
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

  it('rejects an HTTP-successful continuous resume failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({
      ok: true,
      daemon: {
        rc: 3,
        command_status: 'failed',
        error: 'workdir is unavailable',
      },
    })));
    const { api } = await import('../api');

    await expect(api.setContinuous('s-test', true, 'Resume work')).rejects.toThrow(
      'workdir is unavailable',
    );
  });

  it('wires the complete Web administration surface', async () => {
    const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
      if (path.startsWith('/api/trash?')) return Response.json({ entries: [], total: 0 });
      return Response.json({ ok: true, rc: 0, sid: 's-restored' });
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await api.trash();
    await api.setConfig('s-test', 'manager_model', 'gpt-5.6-sol');
    await api.resetManager('s-test');
    await api.setLaunchCwd('s-test', '/workspace');
    await api.setWorkdir('s-test', '/workspace');
    await api.restoreTrash('0:projects_trash/20260712/s-old');

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/trash?query=&limit=100&offset=0',
      '/api/projects/s-test/config/set',
      '/api/projects/s-test/reset',
      '/api/projects/s-test/launch-cwd',
      '/api/projects/s-test/workdir',
      '/api/trash/0%3Aprojects_trash%2F20260712%2Fs-old/restore',
    ]);
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
  });

  it('sends an explicit force flag for immediate manual daemon stop', async () => {
    const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) => Response.json({
      rc: 0,
      command_status: 'applied',
    }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await api.stopDaemon('s-test', false, 7, true);

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ drain: false, force: true, expected_revision: 7 });
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

  it('forwards operator Task mode without running category auto-detection', async () => {
    const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) => new Response(
      'data: {"type":"done","result":{"kind":"task"}}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await api.messageStream('s-test', 'do something', {}, { routeOverride: 'task' });

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      text: 'do something',
      route_override: 'task',
    });
  });

  it('rejects a stream that closes after a delta without a terminal event', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      'data: {"type":"delta","event":{"type":"ui.argus","text":"partial"}}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )));
    const { api } = await import('../api');
    const onDelta = vi.fn();

    await expect(api.messageStream('s-test', 'do something', { onDelta })).rejects.toThrow(
      'ended before a terminal event',
    );
    expect(onDelta).toHaveBeenCalledWith({ type: 'ui.argus', text: 'partial' });
  });

  it('keeps JSON on streamed chunks and reuses attachment ids in message requests', async () => {
    const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/meta') return Response.json(currentMeta);
      if (path.endsWith('/attachments/limits')) return Response.json({
        max_count: 5, max_bytes_per_file: 100 * 1024 * 1024,
        max_total_bytes: 500 * 1024 * 1024, chunk_bytes: 4 * 1024 * 1024,
      });
      if (path.endsWith('/uploads')) {
        expect(JSON.parse(String(init?.body))).toEqual({ name: 'notes.json', mime: 'application/json', size_bytes: 7 });
        return Response.json({ attachment_id: 'att-123456789abc', offset: 0, size_bytes: 7, complete: false });
      }
      if (init?.method === 'PUT') {
        expect(path).toContain('?offset=0');
        expect(init.body).toBeInstanceOf(Blob);
        expect((init.body as Blob).size).toBe(7);
        return Response.json({ attachment_id: 'att-123456789abc', offset: 7, size_bytes: 7, complete: false });
      }
      if (path.endsWith('/complete')) {
        return Response.json({ attachment_id: 'att-123456789abc', offset: 7, size_bytes: 7, complete: true,
          attachment: {
            attachment_id: 'att-123456789abc',
            relative_path: '.argus/attachments/s-test/att-123456789abc/notes.json',
            original_name: 'notes.json',
            stored_name: 'notes.json',
            mime: 'application/json',
            size_bytes: 7,
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
    const file = new File(['[1, 2]\n'], 'notes.json', { type: 'application/json' });

    const upload = await api.uploadAttachments('s-test', [file]);
    const result = await api.message('s-test', 'summarize the note', {
      attachments: upload.attachments.map((attachment) => ({
        attachment_id: attachment.attachment_id,
      })),
    });

    expect(upload.attachments[0].attachment_id).toBe('att-123456789abc');
    expect(result.reply).toBe('ok');
  });

  it('checks the committed offset after a lost chunk response rather than appending twice', async () => {
    const offsets: number[] = [];
    const completed = vi.fn();
    let actual = 0;
    let lost = true;
    const file = new File(['["abcd"]'], 'data.json');
    const state = () => ({ attachment_id: 'att-123456789abc', offset: actual, size_bytes: file.size, complete: false });
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/meta') return Response.json(currentMeta);
      if (path.endsWith('/limits')) return Response.json({ max_count: 5, max_bytes_per_file: 100, max_total_bytes: 500, chunk_bytes: 4 });
      if (path.endsWith('/uploads')) return Response.json(state());
      if (init?.method === 'PUT') {
        offsets.push(Number(new URL(path, 'https://test').searchParams.get('offset')));
        actual += (init.body as Blob).size;
        if (lost) { lost = false; throw new TypeError('response lost'); }
        return Response.json(state());
      }
      if (path.endsWith('/complete')) return Response.json({ ...state(), complete: true, attachment: {
        attachment_id: 'att-123456789abc', original_name: file.name, stored_name: file.name,
        size_bytes: file.size, mime: 'text/plain', relative_path: 'registered/data.txt',
      } });
      return Response.json(state());
    }));
    const { api } = await import('../api');
    const result = await api.uploadAttachments('s-test', [file], undefined, { onUploaded: completed });
    expect(offsets).toEqual([0, 4]);
    expect(completed).toHaveBeenCalledOnce();
    expect(result.attachments[0].size_bytes).toBe(8);
  });

  it('preserves earlier completed files on partial failure and does not upload them on retry', async () => {
    const first = new File(['1'], 'first.json');
    const second = new File(['2'], 'second.json');
    const completed = new Map<File, import('../api').UploadedAttachment>();
    let begins = 0;
    const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/meta') return Response.json(currentMeta);
      if (path.endsWith('/limits')) return Response.json({ max_count: 5, max_bytes_per_file: 100, max_total_bytes: 500, chunk_bytes: 4 });
      if (path.endsWith('/uploads')) {
        begins++;
        if (begins === 2) return Response.json({ detail: 'disk full' }, { status: 507 });
        return Response.json({ attachment_id: 'att-123456789abc', offset: 0, size_bytes: 1, complete: false });
      }
      if (init?.method === 'PUT') return Response.json({ attachment_id: 'att-123456789abc', offset: 1, size_bytes: 1, complete: false });
      return Response.json({ attachment_id: 'att-123456789abc', offset: 1, size_bytes: 1, complete: true, attachment: {
        attachment_id: 'att-123456789abc', original_name: first.name, stored_name: first.name,
        size_bytes: 1, mime: 'text/plain', relative_path: 'registered/first.txt',
      } });
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');
    await expect(api.uploadAttachments('s-test', [first, second], undefined, {
      completed, onUploaded: (file, attachment) => completed.set(file, attachment),
    })).rejects.toThrow('disk full');
    expect(completed.has(first)).toBe(true);
    const before = fetchMock.mock.calls.length;
    await api.uploadAttachments('s-test', [first], undefined, { completed });
    expect(fetchMock.mock.calls.slice(before).map(([path]) => path)).toEqual([]);
  });

  it('cancels an incomplete file without posting a message and reports cleanup failure', async () => {
    const controller = new AbortController();
    const cleanupError = vi.fn();
    const methods: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/meta') return Response.json(currentMeta);
      if (path.endsWith('/limits')) return Response.json({ max_count: 5, max_bytes_per_file: 100, max_total_bytes: 500, chunk_bytes: 4 });
      methods.push(init?.method ?? 'GET');
      if (path.endsWith('/uploads')) return Response.json({ attachment_id: 'att-123456789abc', offset: 0, size_bytes: 1, complete: false });
      if (init?.method === 'PUT') {
        controller.abort();
        throw new DOMException('Aborted', 'AbortError');
      }
      return Response.json({ detail: 'cleanup offline' }, { status: 503 });
    }));
    const { api } = await import('../api');
    await expect(api.uploadAttachments('s-test', [new File(['1'], 'a.json')], controller.signal,
      { onCleanupError: cleanupError })).rejects.toMatchObject({ name: 'AbortError' });
    expect(methods).toEqual(['POST', 'PUT', 'DELETE']);
    expect(cleanupError).toHaveBeenCalledOnce();
  });
});

describe('small attachment batch fast path', () => {
  const limits = { max_count: 5, max_bytes_per_file: 100 * 1024 * 1024, max_total_bytes: 500 * 1024 * 1024 };
  let requests: MultipartRequest[];
  let fetchMock: ReturnType<typeof vi.fn>;

  class MultipartRequest {
    method = '';
    path = '';
    timeout = 0;
    status = 200;
    statusText = 'OK';
    responseText = '';
    release = RELEASE_ID;
    headers: Record<string, string> = {};
    body = new FormData();
    upload: {
      onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null;
      onload: (() => void) | null;
    } = { onprogress: null, onload: null };
    onload: (() => Promise<void>) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    ontimeout: (() => void) | null = null;
    open(method: string, path: string) { this.method = method; this.path = path; }
    setRequestHeader(name: string, value: string) { this.headers[name] = value; }
    getResponseHeader() { return this.release; }
    send(body: FormData) { this.body = body; requests.push(this); }
    abort() { this.onabort?.(); }
    files() {
      return this.body.getAll('files').map(file => {
        if (typeof file === 'string') throw new Error('Expected uploaded file');
        return file;
      });
    }
    async reply(body?: unknown, status = 200) {
      this.status = status;
      this.responseText = JSON.stringify(body ?? { limits, attachments: this.files().map((file, index) => ({
        attachment_id: `att-${String(index).padStart(12, '0')}`,
        original_name: file.name, stored_name: file.name, mime: file.type,
        relative_path: `registered/${file.name}`, size_bytes: file.size,
      })) });
      await this.onload?.();
    }
  }

  beforeEach(() => {
    vi.resetModules();
    requests = [];
    vi.stubGlobal('window', { location: { search: '' } });
    vi.stubGlobal('localStorage', { getItem: () => 'paired-test-token' });
    vi.stubGlobal('XMLHttpRequest', MultipartRequest);
    fetchMock = vi.fn(async (path: string) => {
      if (path === '/api/meta') return Response.json(currentMeta);
      throw new Error(`Chunk path requested: ${path}`);
    });
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('sends five small files in one authenticated request, reports byte progress and reuses receipts', async () => {
    const { api } = await import('../api');
    const files = Array.from({ length: 5 }, (_, i) => new File(['abcd'], `${i}.txt`));
    const completed = new Map<File, import('../api').UploadedAttachment>();
    const onProgress = vi.fn();
    const pending = api.uploadAttachments('s-test', files, undefined, {
      completed, onUploaded: (file, attachment) => completed.set(file, attachment), onProgress,
    });
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    const xhr = requests[0];
    expect([xhr.method, xhr.path]).toEqual(['POST', '/api/projects/s-test/attachments']);
    expect(xhr.headers).toEqual({ Authorization: 'Bearer paired-test-token' });
    expect(xhr.files().map(file => file.name)).toEqual(files.map(file => file.name));
    expect(xhr.files().every(file => file.type === 'text/plain')).toBe(true);
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
    expect(onProgress).toHaveBeenLastCalledWith(expect.objectContaining({ index: 3, loaded: 2, total: 4, phase: 'uploading' }));
    xhr.upload.onload?.();
    expect(onProgress).toHaveBeenLastCalledWith(expect.objectContaining({ index: 5, phase: 'validating' }));
    expect(completed.size).toBe(0);
    await xhr.reply();
    const result = await pending;
    expect(completed.size).toBe(5);
    expect(onProgress).toHaveBeenLastCalledWith(expect.objectContaining({ index: 5, phase: 'ready' }));
    expect(await api.uploadAttachments('s-test', files, undefined, { completed })).toEqual(result);
    expect(requests).toHaveLength(1);
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/meta']);
  });

  it('keeps cached references in file order and uploads only the fresh files', async () => {
    const { api } = await import('../api');
    const files = [new File(['a'], 'fresh.txt'), new File(['b'], 'cached.txt')];
    const cached = { attachment_id: 'att-abcdef123456', original_name: 'cached.txt', stored_name: 'cached.txt',
      mime: 'text/plain', relative_path: 'registered/cached.txt', size_bytes: 1 };
    const onUploaded = vi.fn();
    const pending = api.uploadAttachments('s-test', files, undefined, {
      completed: new Map([[files[1], cached]]), onUploaded,
    });
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].files().map(file => file.name)).toEqual(['fresh.txt']);
    await requests[0].reply();
    const result = await pending;
    expect(result.attachments[1]).toEqual(cached);
    expect(onUploaded).toHaveBeenCalledOnce();
    expect(onUploaded.mock.calls[0][0]).toBe(files[0]);
  });

  it('accepts the exact 10 MiB per-file and 25 MiB batch fast-path boundaries', async () => {
    const { api } = await import('../api');
    const data = new Uint8Array(10 * 1024 * 1024);
    const files = [new File([data], 'a.txt'), new File([data], 'b.txt'),
      new File([data.subarray(0, 5 * 1024 * 1024)], 'c.txt')];
    const pending = api.uploadAttachments('s-test', files);
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].files().reduce((sum, file) => sum + file.size, 0)).toBe(25 * 1024 * 1024);
    await requests[0].reply();
    expect((await pending).attachments).toHaveLength(3);
  });

  it.each(['file', 'batch', 'json'])('keeps %s overflow/resource cases on the chunk protocol', async kind => {
    const { api } = await import('../api');
    const data = new Uint8Array(10 * 1024 * 1024 + 1);
    const files = kind === 'json' ? [new File(['{}'], 'data.JSON')]
      : kind === 'file' ? [new File([data], 'large.txt')]
      : [new File([data.subarray(0, 10 * 1024 * 1024)], 'a.txt'),
        new File([data.subarray(0, 10 * 1024 * 1024)], 'b.txt'),
        new File([data.subarray(0, 5 * 1024 * 1024 + 1)], 'c.txt')];
    await expect(api.uploadAttachments('s-test', files)).rejects.toThrow('/attachments/limits');
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe('/api/projects/s-test/attachments/limits');
    expect(requests).toHaveLength(0);
  });

  it('resumes an existing small partial file instead of starting a new multipart upload', async () => {
    const { api } = await import('../api');
    const data = new Uint8Array(10 * 1024 * 1024);
    const files = [new File([data], 'a.txt'), new File([data], 'b.txt'),
      new File([data.subarray(0, 6 * 1024 * 1024)], 'c.txt')];
    let fail = true, offset = 0, begins = 0, statuses = 0;
    const state = () => ({ attachment_id: 'att-abcdef123456', offset, size_bytes: files[0].size, complete: false });
    fetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/meta') return Response.json(currentMeta);
      if (path.endsWith('/limits')) return Response.json({ ...limits, chunk_bytes: 4 * 1024 * 1024 });
      if (path.endsWith('/uploads')) { begins++; return Response.json(state()); }
      if (init?.method === 'PUT') {
        if (fail) { fail = false; return Response.json({ detail: 'disk full' }, { status: 507 }); }
        if (!(init.body instanceof Blob)) throw new Error('Expected chunk bytes');
        offset += init.body.size;
        return Response.json(state());
      }
      if (path.endsWith('/complete')) return Response.json({ ...state(), complete: true, attachment: {
        attachment_id: 'att-abcdef123456', original_name: 'a.txt', stored_name: 'a.txt',
        mime: 'text/plain', relative_path: 'registered/a.txt', size_bytes: files[0].size,
      } });
      statuses++;
      return Response.json(state());
    });
    await expect(api.uploadAttachments('s-test', files)).rejects.toThrow('disk full');
    expect((await api.uploadAttachments('s-test', [files[0]])).attachments[0].size_bytes).toBe(files[0].size);
    expect([begins, statuses, requests.length]).toEqual([1, 1, 0]);
  });

  it('rejects an oversized combined draft before starting multipart', async () => {
    const { api } = await import('../api');
    const ready = { attachment_id: 'att-abcdef123456', original_name: 'ready.txt', stored_name: 'ready.txt',
      mime: 'text/plain', relative_path: 'registered/ready.txt', size_bytes: 500 * 1024 * 1024 };
    await expect(api.uploadAttachments('s-test', [new File(['a'], 'new.txt')], undefined, { ready: [ready] }))
      .rejects.toThrow('Attachments exceed');
    expect(requests).toHaveLength(0);
  });

  it('cancels the request without confirming files or sending a message', async () => {
    const { api } = await import('../api');
    const controller = new AbortController();
    const onUploaded = vi.fn();
    const pending = expect(api.uploadAttachments('s-test', [new File(['a'], 'a.txt')],
      controller.signal, { onUploaded })).rejects.toMatchObject({ name: 'AbortError' });
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    controller.abort();
    await pending;
    expect(onUploaded).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/meta']);
  });

  it.each(['onerror', 'ontimeout'] as const)('does not blindly replay an unconfirmed batch after %s', async event => {
    const { api } = await import('../api');
    const onUploaded = vi.fn();
    const pending = expect(api.uploadAttachments('s-test', [new File(['a'], 'a.txt')], undefined, { onUploaded }))
      .rejects.toThrow('no automatic retry');
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    requests[0][event]?.();
    await pending;
    expect(onUploaded).not.toHaveBeenCalled();
    expect(requests).toHaveLength(1);
  });

  it('surfaces the server error and refuses incomplete receipts', async () => {
    const { api } = await import('../api');
    const files = [new File(['a'], 'a.txt')];
    const failed = expect(api.uploadAttachments('s-test', files)).rejects.toMatchObject({ status: 413, detail: 'too large' });
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    await requests[0].reply({ detail: 'too large' }, 413);
    await failed;
    const onUploaded = vi.fn();
    const incomplete = expect(api.uploadAttachments('s-test', files, undefined, { onUploaded }))
      .rejects.toThrow('complete attachment batch');
    await vi.waitFor(() => expect(requests).toHaveLength(2));
    await requests[1].reply({ attachments: [], limits });
    await incomplete;
    expect(onUploaded).not.toHaveBeenCalled();
  });
});

describe('a page left open across a deployment', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal('window', { location: { search: '' } });
    vi.stubGlobal('localStorage', { getItem: () => null });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('detects a newer snapshot after the cached handshake and stops old option parsing', async () => {
    let updated = false;
    const fetchMock = vi.fn(async (path: string) => Response.json(
      path === '/api/meta' ? currentMeta : path.includes('/snapshot') ? currentSnapshot : { projects: [] },
      { headers: { 'X-Argus-Release': updated ? 'next-release' : RELEASE_ID } },
    ));
    vi.stubGlobal('fetch', fetchMock);
    const { api, LocalArgusUnavailableError } = await import('../api');
    const { getPageUpdateAvailable, observePageRelease, PageUpdateRequiredError } = await import('../lib/pageUpdate');
    await api.listProjects();
    updated = true;
    const failure = await api.snapshot('s-one').catch(error => error);
    expect(failure).toBeInstanceOf(PageUpdateRequiredError);
    expect(failure).not.toBeInstanceOf(LocalArgusUnavailableError);
    expect(getPageUpdateAvailable()).toBe(true);
    observePageRelease(RELEASE_ID); // A delayed response from before deployment.
    expect(getPageUpdateAvailable()).toBe(true);
    expect(fetchMock.mock.calls.filter(([path]) => path === '/api/meta')).toHaveLength(1);
    const calls = fetchMock.mock.calls.length;
    await expect(api.answerDomain('s-one', 'intake-1', 'build', '')).rejects.toThrow(/Refresh/);
    expect(fetchMock).toHaveBeenCalledTimes(calls);
  });

  it('delivers an already accepted mutation receipt while raising the update notice', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(
      { kind: 'task', reply: 'Accepted' }, { headers: { 'X-Argus-Release': 'next-release' } },
    )));
    const { api } = await import('../api');
    const { getPageUpdateAvailable } = await import('../lib/pageUpdate');
    expect(await api.answerDomain('s-one', 'intake-1', 'direct', '')).toEqual({ kind: 'task', reply: 'Accepted' });
    expect(getPageUpdateAvailable()).toBe(true);
  });
});
