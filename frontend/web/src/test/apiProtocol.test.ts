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
    package_version: '0.1.0',
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

describe('web API protocol handshake', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal('window', { location: { search: '' } });
    vi.stubGlobal('localStorage', { getItem: () => null });
  });

  afterEach(() => vi.unstubAllGlobals());

  it('rejects an old backend before requesting projects', async () => {
    const fetchMock = vi.fn(async () => new Response('not found', { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');

    await expect(api.listProjects()).rejects.toThrow(/does not expose \/api\/meta/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('performs one compatible handshake before project reads', async () => {
    const fetchMock = vi.fn(async (path: string) => {
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

  it('passes cancellation signals to project reads', async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ events: [] }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');
    const controller = new AbortController();

    await expect(api.events('s-test', 120, controller.signal)).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/projects/s-test/events?limit=120',
      expect.objectContaining({ signal: controller.signal }),
    );
  });
});
