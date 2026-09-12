import { afterEach, describe, expect, it, vi } from 'vitest';
import { AdminAPIError, adminAPI, observationFilename } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('isolated administrator API', () => {
  it('uses only administrator routes and same-origin cookies without frontend credentials or handshake', async () => {
    const readToken = vi.fn(() => { throw new Error('User token must not be read'); });
    vi.stubGlobal('localStorage', { getItem: readToken });
    vi.stubGlobal('window', { location: { search: '?token=frontend-secret' } });
    const fetchMock = vi.fn(async (path: string, _init: RequestInit) => Response.json(
      path === '/admin/status' ? { role: 'admin', key_id: 'admin', readonly: false, expires_at: 123 } : {},
    ));
    vi.stubGlobal('fetch', fetchMock);
    await adminAPI.overview({ tenant: 'trial-01', query: 'paper & proof', offset: 20 });
    await adminAPI.detail({ tenant: 'trial-01', sid: 's-one', taskId: 'task-1' });
    await adminAPI.observations({ tenant: 'trial-01', sid: 's-one', cursor: '12:30', limit: 100 });
    await adminAPI.identity();
    await adminAPI.collector();
    await adminAPI.audit({ limit: 12 });
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/admin/api/training/collaboration?purpose=internal_training&offset=20&tenant=trial-01&query=paper+%26+proof',
      '/admin/api/training/collaboration/trial-01/s-one?purpose=internal_training&task_id=task-1',
      '/admin/api/training/observations/trial-01/s-one?purpose=internal_training&cursor=12%3A30&limit=100',
      '/admin/status', '/admin/api/research/status', '/admin/api/training/audit?limit=12',
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init).toMatchObject({ credentials: 'same-origin', method: 'GET', cache: 'no-store', redirect: 'error' });
      expect(new Headers(init.headers).has('Authorization')).toBe(false);
      expect(new Headers(init.headers).has('Cookie')).toBe(false);
      expect(init.signal).toBeInstanceOf(AbortSignal);
    }
    expect(readToken).not.toHaveBeenCalled();
  });

  it('returns a safe administrator login error on 401 without using a server-provided redirect', async () => {
    const replace = vi.fn();
    vi.stubGlobal('window', { location: { replace } });
    const fetchMock = vi.fn(async () => Response.json({ detail: 'https://untrusted.example/login', redirect: '//untrusted.example' }, { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(adminAPI.identity()).rejects.toMatchObject({
      name: 'AdminAPIError', status: 401, code: 'admin_authentication_required', loginURL: '/admin/login',
      message: '数据后台登录已过期，请重新登录。',
    });
    expect(replace).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('fails closed if a non-admin or malformed identity is returned', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ role: 'trial', readonly: false })));
    await expect(adminAPI.identity()).rejects.toMatchObject({ status: 502, code: 'invalid_admin_response' });
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ role: 'admin' })));
    await expect(adminAPI.identity()).rejects.toBeInstanceOf(AdminAPIError);
  });

  it('reads the original project names from the administrator metadata endpoint', async () => {
    const body = {
      projects: [{ id: 's-one', title: 'Multi-Agent 通信论文', display_name: 'Multi-Agent 通信论文', research_deleted: false }],
      state: 'ok', truncated: false, skipped: 0, policy: {},
    };
    const fetchMock = vi.fn(async (_path: string, _init: RequestInit) => Response.json(body));
    vi.stubGlobal('fetch', fetchMock);
    expect(await adminAPI.projectDirectory('trial-11')).toEqual(body);
    expect(fetchMock.mock.calls[0][0]).toBe('/admin/api/tenants/trial-11/projects');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin' });
    expect(new Headers(fetchMock.mock.calls[0][1].headers).has('Authorization')).toBe(false);
  });

  it('reads the separate user cookie without bearer auth, handshake, or account switching', async () => {
    const readToken = vi.fn(() => 'frontend-secret');
    const replace = vi.fn();
    vi.stubGlobal('localStorage', { getItem: readToken });
    vi.stubGlobal('window', { location: { search: '?token=frontend-secret', replace } });
    const fetchMock = vi.fn(async (_path: string, _init: RequestInit) => Response.json({
      key_id: 'trial-11', role: 'trial', readonly: false, tokens_remaining: 200,
    }));
    vi.stubGlobal('fetch', fetchMock);
    expect(await adminAPI.workspaceIdentity()).toEqual({ key_id: 'trial-11', role: 'trial', readonly: false });
    expect(fetchMock.mock.calls[0][0]).toBe('/invite/status');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin', redirect: 'error' });
    expect(new Headers(fetchMock.mock.calls[0][1].headers).has('Authorization')).toBe(false);
    expect(readToken).not.toHaveBeenCalled();
    fetchMock.mockImplementation(async () => Response.json({ detail: '请先输入邀请码' }, { status: 401 }));
    const error = await adminAPI.workspaceIdentity().catch((value: unknown) => value);
    expect(error).toMatchObject({ status: 401 });
    expect(error).not.toBeInstanceOf(AdminAPIError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(replace).not.toHaveBeenCalled();
  });

  it('preserves cancellation for a React Query request', async () => {
    const controller = new AbortController();
    vi.stubGlobal('fetch', vi.fn((_path: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true });
    })));
    const reason = new DOMException('Project changed', 'AbortError');
    const request = adminAPI.observations({ tenant: 'trial-01', sid: 's-one', signal: controller.signal });
    controller.abort(reason);
    await expect(request).rejects.toBe(reason);
  });

  it('keeps readonly/forbidden responses forbidden instead of retrying with ordinary-user auth', async () => {
    const fetchMock = vi.fn(async () => Response.json({ detail: 'Read-only session' }, { status: 403 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(adminAPI.exportObservations({ purpose: 'internal_training', projects: [{ tenant_id: 'trial-01', sid: 's-one' }] }))
      .rejects.toMatchObject({ status: 403, loginURL: null });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('original observation ZIP downloads', () => {
  it('posts the exact project scope with the required JSON header and returns the audited ZIP response', async () => {
    const fetchMock = vi.fn(async (_path: string, _init: RequestInit) => new Response('zip-bytes', {
      headers: { 'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="observations-20260912.zip"' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await adminAPI.exportObservations({
      purpose: 'internal_training', projects: [{ tenant_id: 'trial-01', sid: 's-one' }, { tenant_id: 'trial-02', sid: 's-one' }],
    });
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe('/admin/api/training/export-observations');
    expect(init).toMatchObject({ method: 'POST', credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
    expect(new Headers(init.headers).get('Accept')).toBe('application/zip');
    expect(JSON.parse(String(init.body))).toEqual({
      purpose: 'internal_training', projects: [{ tenant_id: 'trial-01', sid: 's-one' }, { tenant_id: 'trial-02', sid: 's-one' }],
    });
    expect(result.filename).toBe('observations-20260912.zip');
    expect(await result.blob.text()).toBe('zip-bytes');
    expect(result.blob.type).toBe('application/zip');
  });

  it('does not download an HTML login page or JSON error as a ZIP', async () => {
    for (const type of ['text/html', 'application/json']) {
      vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { headers: { 'Content-Type': type } })));
      await expect(adminAPI.exportObservations({ purpose: 'internal_training', projects: [] }))
        .rejects.toMatchObject({ code: 'invalid_admin_response' });
    }
  });

  it('supports UTF-8 names and rejects path, control character, and non-ZIP filenames', () => {
    expect(observationFilename("attachment; filename*=UTF-8''%E8%BF%87%E7%A8%8B.zip")).toBe('过程.zip');
    for (const disposition of [null, 'attachment; filename="../../secret.zip"',
      'attachment; filename="C:\\temp\\data.zip"', 'attachment; filename="file.html"',
      "attachment; filename*=UTF-8''bad%0Aname.zip", 'attachment; filename=".hidden.zip"']) {
      expect(observationFilename(disposition)).toBe('argus-observations.zip');
    }
  });
});
