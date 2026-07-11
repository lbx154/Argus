import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const currentMeta = {
  service: 'argus-skill-webapi',
  protocol: { name: 'argus.webapi', major: 1, minor: 3 },
  snapshot_schema_version: 2,
  capabilities: [
    'daemon.admission.v1',
    'daemon.status.protocol.v1',
    'cost.reservation.v1',
    'event.catalog.v1',
    'event.payload-schema.v1',
    'manager.sse.v1',
    'snapshot.budget.v1',
    'snapshot.schema.v1',
    'usage.recorded.v2',
  ],
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
});
