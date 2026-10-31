import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { connectionProblemAfterRead, installDesktopConnectionRecovery } from '../lib/desktopRecovery';
import { LocalArgusUnavailableError } from '../api';
import { backendRecoveryNotifications } from '../../../../desktop-tauri/src/backendRecovery';

const disposers: Array<() => void> = [];
afterEach(() => { disposers.splice(0).reverse().forEach(dispose => dispose()); });
function fixture(href = 'http://127.0.0.1:8799/', embedded = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const target = Object.assign(new EventTarget(), { location: new URL(href), parent: { postMessage: vi.fn() } });
  if (!embedded) Object.assign(target, { parent: target });
  disposers.push(() => client.clear(), installDesktopConnectionRecovery(client, target as unknown as Window));
  const ready = (generation = 1, overrides: Record<string, unknown> = {}, message: Record<string, unknown> = {}) => {
    const event = Object.assign(new Event('message'), { source: target.parent, origin: 'http://tauri.localhost',
      data: { type: 'argus:backend-ready', version: 1, generation, origin: target.location.origin, ...message }, ...overrides });
    target.dispatchEvent(event);
  };
  return { client, target, ready };
}
function observe(client: QueryClient, key: string, fn: (ctx: { signal: AbortSignal }) => Promise<unknown>) {
  const observer = new QueryObserver(client, { queryKey: [key], queryFn: fn });
  disposers.push(observer.subscribe(() => {}));
  return observer;
}
const failure = () => new LocalArgusUnavailableError('GET', '/api/projects');

describe('desktop-only read recovery', () => {
  it('retains a no-data failure during desktop recovery, without changing ordinary web pending state', () => {
    const error = failure();
    expect(connectionProblemAfterRead(error, { status: 'pending', error: null }, true)).toBe(error);
    expect(connectionProblemAfterRead(error, { status: 'success', error: null }, true)).toBeUndefined();
    expect(connectionProblemAfterRead(error, { status: 'pending', error: null }, false)).toBeUndefined();
  });
  it('keeps a real outage visible until refreshed GETs actually succeed', async () => {
    const { client, ready } = fixture();
    client.setQueryData(['projects'], { projects: ['cached'] });
    let resolve!: (value: unknown) => void;
    const fn = vi.fn().mockRejectedValueOnce(failure()).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    observe(client, 'projects', fn);
    await vi.waitFor(() => expect(client.getQueryState(['projects'])?.status).toBe('error'));
    ready();
    await vi.waitFor(() => expect(fn).toHaveBeenCalledTimes(2));
    expect(client.getQueryState(['projects'])?.error).toBeInstanceOf(LocalArgusUnavailableError);
    resolve({ projects: [] });
    await vi.waitFor(() => expect(client.getQueryState(['projects'])?.status).toBe('success'));
    expect(client.getQueryState(['projects'])?.error).toBeNull();
  });

  it('aborts old reads and ignores a late failure from the retired backend', async () => {
    const { client, ready } = fixture();
    client.setQueryData(['projects'], { projects: ['cached'] });
    let rejectOld!: (error: Error) => void;
    let oldSignal!: AbortSignal;
    const fn = vi.fn().mockImplementationOnce(({ signal }) => { oldSignal = signal; return new Promise((_resolve, reject) => { rejectOld = reject; }); })
      .mockResolvedValue({ projects: ['fresh'] });
    observe(client, 'projects', fn);
    ready();
    await vi.waitFor(() => expect(client.getQueryData(['projects'])).toEqual({ projects: ['fresh'] }));
    expect(oldSignal.aborted).toBe(true);
    rejectOld(failure());
    await new Promise(done => setTimeout(done, 10));
    expect(client.getQueryState(['projects'])?.error).toBeNull();
    expect(client.getQueryData(['projects'])).toEqual({ projects: ['fresh'] });
  });

  it.each([failure(), Object.assign(new Error('Unauthorized'), { status: 401 })])('does not hide a persistent read/authentication failure: %s', async error => {
    const { client, ready } = fixture();
    const fn = vi.fn().mockRejectedValue(error);
    observe(client, 'project-costs', fn);
    await vi.waitFor(() => expect(client.getQueryState(['project-costs'])?.status).toBe('error'));
    ready(); ready(); ready(0);
    await vi.waitFor(() => expect(fn).toHaveBeenCalledTimes(2));
    expect(client.getQueryState(['project-costs'])?.error).toBe(error);
    await new Promise(done => setTimeout(done, 20));
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('does not replay mutations or unrelated/generative queries', async () => {
    const { client, ready } = fixture();
    const write = vi.fn().mockResolvedValue('accepted');
    await client.getMutationCache().build(client, { mutationFn: write }).execute(undefined);
    const generation = vi.fn().mockResolvedValue('generated');
    observe(client, 'generate-report', generation);
    await vi.waitFor(() => expect(client.getQueryData(['generate-report'])).toBe('generated'));
    ready(); ready(2);
    await new Promise(done => setTimeout(done, 20));
    expect(write).toHaveBeenCalledTimes(1);
    expect(generation).toHaveBeenCalledTimes(1);
  });

  it('rejects untrusted sources/origins, wrong endpoints, malformed/duplicate generations', async () => {
    const { client, ready } = fixture();
    const cancel = vi.spyOn(client, 'cancelQueries');
    ready(1, { source: {} }); ready(1, { origin: 'https://trial.example' });
    ready(1, { origin: 'http://tauri.localhost.evil' });
    ready(1, {}, { origin: 'http://127.0.0.1:9999' });
    ready(1, {}, { version: 2 }); ready(-1); ready(1.1);
    expect(cancel).not.toHaveBeenCalled();
    ready(2); ready(2); ready(1);
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it.each([['https://trial.example/', true], ['http://127.0.0.1:8799/', false]])('leaves ordinary/hosted web untouched: %s', (href, embedded) => {
    const { client, ready } = fixture(String(href), Boolean(embedded));
    const cancel = vi.spyOn(client, 'cancelQueries'); ready();
    expect(cancel).not.toHaveBeenCalled();
  });
});

describe('native recovery notifications', () => {
  const url = 'http://127.0.0.1:8799/';
  it('covers visible-cockpit early returns, automatic recovery and warning recovery without heartbeat storms', () => {
    const send = vi.fn();
    const notifier = backendRecoveryNotifications(send, () => new URL(url).origin);
    const ready = { state: 'ready' as const, message: 'ready', pid: 1, url };
    notifier.observe(ready); expect(send).not.toHaveBeenCalled();
    notifier.listening(); notifier.listening(); notifier.observe(ready);
    expect(send).toHaveBeenCalledTimes(1);
    notifier.observe({ state: 'starting', message: 'restart' });
    notifier.observe({ ...ready, pid: 2 }); notifier.observe({ ...ready, pid: 2 });
    expect(send).toHaveBeenCalledTimes(2);
    notifier.observe({ ...ready, pid: 2, warning: 'disconnected' });
    notifier.observe({ ...ready, pid: 2 });
    expect(send).toHaveBeenCalledTimes(3);
  });
  it('does not send to an old port and replays readiness to a newly listening document', () => {
    const send = vi.fn(); let origin = 'http://127.0.0.1:9999';
    const notifier = backendRecoveryNotifications(send, () => origin);
    notifier.listening(); notifier.observe({ state: 'ready', message: 'ready', url });
    expect(send).not.toHaveBeenCalled();
    notifier.navigating(); origin = new URL(url).origin;
    notifier.listening(); expect(send).toHaveBeenCalledTimes(1);
  });
});
