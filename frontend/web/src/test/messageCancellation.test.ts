import { afterEach, expect, it, vi } from 'vitest';
import { api, newRequestId } from '../api';

afterEach(() => vi.unstubAllGlobals());

it('creates an admissible message identity when randomUUID is unavailable on plain HTTP', () => {
  vi.stubGlobal('crypto', {});
  const first = newRequestId();
  expect(first).toMatch(/^[A-Za-z0-9_-]{1,128}$/);
  expect(newRequestId()).not.toBe(first);
});

it('sends the message with its request identity, on the signal that withdraws the page’s request', async () => {
  vi.stubGlobal('window', { location: { search: '' } });
  vi.stubGlobal('localStorage', { getItem: () => null });
  const controller = new AbortController();
  vi.stubGlobal('fetch', vi.fn(async (_path: string, init: RequestInit) => {
    expect(JSON.parse(init.body as string)).toEqual({ text: 'Old goal', request_id: 'old-request' });
    expect(init.signal).toBe(controller.signal);
    controller.abort();
    throw new DOMException('Aborted', 'AbortError');
  }));
  await expect(api.messageStream('project', 'Old goal', {}, {
    signal: controller.signal, requestId: 'old-request',
  })).rejects.toMatchObject({ name: 'AbortError' });
});
