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
      '/api/projects/s-test/events?limit=120&view=ui',
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it('wires the complete Web administration surface', async () => {
    const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
      if (path === '/api/metrics') return Response.json({ slo: { status: 'healthy' } });
      if (path === '/api/trash') return Response.json({ entries: [] });
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
    await api.setIdentity('s-test', 'operator');
    await api.resetManager('s-test');
    await api.skills('s-test', 'ls');
    await api.setLaunchCwd('s-test', '/workspace');
    await api.replaceDaemon('s-test', 's-victim');
    await api.upgradeDaemon('s-test');
    await api.restoreTrash('0:projects_trash/20260712/s-old');

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/metrics',
      '/api/trash',
      '/api/projects/s-test/plan',
      '/api/projects/s-test/config/set',
      '/api/projects/s-test/identity',
      '/api/projects/s-test/reset',
      '/api/projects/s-test/skills',
      '/api/projects/s-test/launch-cwd',
      '/api/projects/s-test/daemon/replace',
      '/api/projects/s-test/daemon/upgrade',
      '/api/trash/0%3Aprojects_trash%2F20260712%2Fs-old/restore',
    ]);
    const replaceBody = JSON.parse(String(fetchMock.mock.calls[8][1]?.body));
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
});
