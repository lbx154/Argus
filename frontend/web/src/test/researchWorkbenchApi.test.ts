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

const snapshot = {
  schema_version: SNAPSHOT_SCHEMA_VERSION,
  daemon: {},
  spend_usd: 0,
  spend_status: 'ok',
  partial: false,
  diagnostics: [],
};

describe('research workbench API resilience', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal('window', { location: { search: '' } });
    vi.stubGlobal('localStorage', { getItem: () => null });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('performs the protocol handshake before listing projects', async () => {
    const fetchMock = vi.fn(async (path: string) => Response.json(
      path === '/api/meta' ? currentMeta : { projects: [] },
    ));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../research-workbench/api');

    await expect(api.projects()).resolves.toEqual({ projects: [] });
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/meta',
      '/api/projects',
    ]);
  });

  it('shares the URL-paired in-memory token when localStorage is unavailable', async () => {
    vi.stubGlobal('window', {
      location: {
        search: '?token=paired-memory-only',
        pathname: '/',
        hash: '',
      },
      history: { replaceState: vi.fn() },
    });
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('storage blocked'); },
      setItem: () => { throw new Error('storage blocked'); },
    });
    const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get('Authorization')).toBe('Bearer paired-memory-only');
      return Response.json(path === '/api/meta' ? currentMeta : { projects: [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    const primary = await import('../api');
    primary.adoptTokenFromUrl();
    const { api } = await import('../research-workbench/api');

    await expect(api.projects()).resolves.toEqual({ projects: [] });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    { rc: 2, command_status: 'failed' },
    { rc: 0, command_status: 'rejected' },
    { rc: 2 },
  ])('surfaces HTTP 200 executor failures ($command_status, rc=$rc)', async (result) => {
    const error = 'background executor failed to start: launcher diagnostic';
    vi.stubGlobal('fetch', vi.fn(async (path: string) => Response.json(
      path === '/api/meta' ? currentMeta : { ...result, error },
    )));
    const { api } = await import('../research-workbench/api');

    await expect(api.startDaemon('s-failed')).rejects.toThrow(error);
    await expect(api.stopDaemon('s-failed', false)).rejects.toThrow(error);
  });

  it('preserves successful executor command responses', async () => {
    const result = { rc: 0, command_status: 'succeeded' };
    vi.stubGlobal('fetch', vi.fn(async (path: string) => Response.json(
      path === '/api/meta' ? currentMeta : result,
    )));
    const { api } = await import('../research-workbench/api');

    await expect(api.startDaemon('s-ready')).resolves.toEqual(result);
    await expect(api.stopDaemon('s-ready', false)).resolves.toEqual(result);
  });

  it('times out a stalled snapshot and permits the next poll to recover', async () => {
    vi.useFakeTimers();
    const snapshotPath = '/api/projects/s-stalled/snapshot?events_limit=40&compact=false';
    let attempts = 0;
    const fetchMock = vi.fn((path: string, init?: RequestInit): Promise<Response> => {
      if (path === '/api/meta') return Promise.resolve(Response.json(currentMeta));
      if (path !== snapshotPath) return Promise.reject(new Error(`unexpected request: ${path}`));
      attempts += 1;
      if (attempts === 1) {
        return new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
        });
      }
      return Promise.resolve(Response.json(snapshot));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../research-workbench/api');

    const stalled = expect(api.snapshot('s-stalled')).rejects.toThrow(
      /compact=false.*timed out after 12s/,
    );
    await vi.advanceTimersByTimeAsync(12_001);
    await stalled;

    await expect(api.snapshot('s-stalled')).resolves.toMatchObject({
      schema_version: SNAPSHOT_SCHEMA_VERSION,
      partial: false,
    });
    expect(attempts).toBe(2);
  });
});
