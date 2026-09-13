import { afterEach, expect, it, vi } from 'vitest';
import { api, newRequestId } from '../api';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it('creates an admissible message identity when randomUUID is unavailable on plain HTTP', () => {
  vi.stubGlobal('crypto', {});
  const first = newRequestId();
  expect(first).toMatch(/^[A-Za-z0-9_-]{1,128}$/);
  expect(newRequestId()).not.toBe(first);
});

it('sends request identity to Manager and keeps its cancellation independent of the aborted stream', async () => {
  vi.stubGlobal('window', { location: { search: '' } });
  vi.stubGlobal('localStorage', { getItem: () => null });
  const controller = new AbortController();
  const fetch = vi.fn(async (path: string, init: RequestInit) => {
    if (path.endsWith('/cancel')) {
      expect(init.signal).not.toBe(controller.signal);
      expect(init.signal?.aborted).toBe(false);
      return new Response(JSON.stringify({ requested: true, status: 'cancelled' }));
    }
    expect(JSON.parse(init.body as string)).toEqual({ text: 'Old goal', request_id: 'old-request' });
    expect(init.signal).toBe(controller.signal);
    controller.abort();
    throw new DOMException('Aborted', 'AbortError');
  });
  vi.stubGlobal('fetch', fetch);
  await expect(api.messageStream('project', 'Old goal', {}, {
    signal: controller.signal, requestId: 'old-request',
  })).rejects.toMatchObject({ name: 'AbortError' });
  await expect(api.cancelMessage('project', 'old-request')).resolves.toMatchObject({ requested: true });
  expect(fetch.mock.calls[1][0]).toBe('/api/projects/project/message/cancel');
  expect(JSON.parse(fetch.mock.calls[1][1].body as string)).toEqual({ request_id: 'old-request' });
});

it('reports an unreachable cancellation endpoint within five seconds without replaying the request', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', { location: { search: '' } });
  vi.stubGlobal('localStorage', { getItem: () => null });
  const fetch = vi.fn(() => new Promise<Response>(() => {}));
  vi.stubGlobal('fetch', fetch);
  const failure = api.cancelMessage('project', 'request').catch(error => error);
  await vi.advanceTimersByTimeAsync(5000);
  expect(await failure).toBeInstanceOf(Error);
  expect(fetch).toHaveBeenCalledTimes(1);
});
