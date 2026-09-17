import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The vertical store client: paths, bodies, the bearer header and how a 409
// detail survives into the error the page shows.
describe('vertical store API client', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal('window', { location: { search: '' } });
    vi.stubGlobal('localStorage', { getItem: () => 'paired-token' });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reads the list with the bearer header', async () => {
    const fetchMock = vi.fn(async () => Response.json({ verticals: [], catalog: {}, host: {} }));
    vi.stubGlobal('fetch', fetchMock);
    const { api, VERTICAL_STORE_CAPABILITY } = await import('../api');
    expect(VERTICAL_STORE_CAPABILITY).toBe('verticals.store.v1');
    await expect(api.verticals()).resolves.toMatchObject({ verticals: [] });
    expect(fetchMock.mock.calls[0]).toEqual([
      '/api/verticals',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer paired-token' }) }),
    ]);
  });

  it('posts manage actions with an encoded name and an explicit force flag only when asked', async () => {
    const fetchMock = vi.fn(async () => Response.json({ name: 'x', action: 'uninstall', operation: null }, { status: 202 }));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');
    await api.manageVertical('kernel engineering', 'uninstall');
    await api.manageVertical('kernel engineering', 'uninstall', { force: true });
    await api.manageVertical('materials', 'enable', { force: false });
    const calls = fetchMock.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(calls.map(([path]) => path)).toEqual([
      '/api/verticals/kernel%20engineering/manage/uninstall',
      '/api/verticals/kernel%20engineering/manage/uninstall',
      '/api/verticals/materials/manage/enable',
    ]);
    expect(calls.map(([, init]) => init.body)).toEqual(['{}', '{"force":true}', '{}']);
    expect(calls[0][1].method).toBe('POST');
    expect(calls[0][1].headers).toMatchObject({ 'Content-Type': 'application/json', Authorization: 'Bearer paired-token' });
  });

  it('keeps the 409 detail readable on the error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'used by projects s-1, s-2' }, { status: 409 })));
    // Modules were reset above, so take ApiError from the same graph the client uses.
    const [{ api }, { ApiError }] = await Promise.all([import('../api'), import('../../../core/src/http')]);
    const failure = await api.manageVertical('kernel_engineering', 'uninstall').catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, detail: 'used by projects s-1, s-2' });
    expect((failure as InstanceType<typeof ApiError>).message).toContain('used by projects s-1, s-2');
  });

  it('refreshes the catalog and reads one operation', async () => {
    const fetchMock = vi.fn(async (path: string) => Response.json(
      path.endsWith('/operation')
        ? { status: 'running', action: 'install', progress: 50, message: null, started: '2026-09-14T08:00:00Z', finished: null }
        : { verticals: [], catalog: { source: 'fresh' }, host: {} },
    ));
    vi.stubGlobal('fetch', fetchMock);
    const { api } = await import('../api');
    await expect(api.refreshVerticalCatalog()).resolves.toMatchObject({ catalog: { source: 'fresh' } });
    await expect(api.verticalOperation('materials')).resolves.toMatchObject({ status: 'running', progress: 50 });
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/verticals/catalog/refresh',
      '/api/verticals/materials/operation',
    ]);
    expect((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].method).toBe('POST');
  });
});
