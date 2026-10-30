import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { QUERY_CACHE_KEY, QUERY_CACHE_MAX_CHARS, persistQueryCache, restoreQueryCache } from '../lib/queryPersistence';
import { version } from '../../../../packages/core/package.json';

function memoryStorage(fail = false) {
  const values = new Map<string, string>();
  return {
    values,
    storage: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => {
        if (fail) throw new DOMException('full', 'QuotaExceededError');
        values.set(key, value);
      },
      removeItem: (key: string) => { values.delete(key); },
    } as unknown as Storage,
  };
}

function timers() {
  const pending = new Map<number, () => void>();
  let next = 0;
  const listeners = new Map<string, () => void>();
  return {
    target: {
      setTimeout: ((callback: () => void) => { pending.set(++next, callback); return next; }) as unknown as Window['setTimeout'],
      clearTimeout: ((id: number) => { pending.delete(id); }) as Window['clearTimeout'],
      addEventListener: ((name: string, listener: () => void) => { listeners.set(name, listener); }) as Window['addEventListener'],
      removeEventListener: ((name: string) => { listeners.delete(name); }) as Window['removeEventListener'],
    },
    run: () => { const callbacks = [...pending.values()]; pending.clear(); callbacks.forEach(callback => callback()); },
    fire: (name: string) => listeners.get(name)?.(),
  };
}

describe('conversation cache across reloads', () => {
  it('restores the last project views as stale data, so they show at once and refresh in the background', () => {
    const { storage, values } = memoryStorage();
    const clock = timers();
    const before = new QueryClient();
    const stop = persistQueryCache(before, () => storage, version, clock.target);
    before.setQueryData(['snapshot', 's-1'], { session: { id: 's-1' } }, { updatedAt: 1_000 });
    before.setQueryData(['conversation', 's-1', 120], [{ role: 'operator', text: 'Hello', ts: 1 }], { updatedAt: 1_000 });
    before.setQueryData(['artifacts', 's-1'], [{ path: 'big.bin' }], { updatedAt: 1_000 });
    before.setQueryData(['events', 's-1'], [{ type: 'ui.argus', text: 'Old event window' }], { updatedAt: 1_000 });
    clock.run();
    stop();
    expect(values.has(QUERY_CACHE_KEY)).toBe(true);
    expect(JSON.parse(values.get(QUERY_CACHE_KEY)!).build).toBe(version);

    const after = new QueryClient({ defaultOptions: { queries: { staleTime: 3_000 } } });
    expect(restoreQueryCache(after, () => storage, version, 2_000)).toBe(2);
    expect(after.getQueryData(['conversation', 's-1', 120])).toEqual([{ role: 'operator', text: 'Hello', ts: 1 }]);
    expect(after.getQueryState(['snapshot', 's-1'])?.dataUpdatedAt).toBe(1_000);
    expect(after.getQueryData(['artifacts', 's-1'])).toBeUndefined();
    expect(after.getQueryData(['events', 's-1'])).toBeUndefined();
    // Another build may read different shapes: it starts clean.
    expect(restoreQueryCache(new QueryClient(), () => storage, `${version}-next`, 2_000)).toBe(0);
  });

  it('keeps the newest views within the size cap and writes before the page goes away', () => {
    const { storage, values } = memoryStorage();
    const clock = timers();
    const client = new QueryClient();
    persistQueryCache(client, () => storage, 'build-1', clock.target);
    const big = 'x'.repeat(Math.floor(QUERY_CACHE_MAX_CHARS * 0.6));
    client.setQueryData(['snapshot', 'older'], { text: big }, { updatedAt: 1_000 });
    client.setQueryData(['snapshot', 'newer'], { text: big }, { updatedAt: 2_000 });
    clock.fire('pagehide');
    const stored = values.get(QUERY_CACHE_KEY)!;
    expect(stored.length).toBeLessThanOrEqual(QUERY_CACHE_MAX_CHARS + 100);
    const restored = new QueryClient();
    restoreQueryCache(restored, () => storage, 'build-1', 3_000);
    expect(restored.getQueryData(['snapshot', 'newer'])).toEqual({ text: big });
    expect(restored.getQueryData(['snapshot', 'older'])).toBeUndefined();
  });

  it('treats full, blocked or corrupt storage as an empty cache', () => {
    const clock = timers();
    const client = new QueryClient();
    persistQueryCache(client, () => memoryStorage(true).storage, 'build-1', clock.target);
    client.setQueryData(['projects'], { projects: [] });
    expect(() => clock.run()).not.toThrow();
    const blocked = () => { throw new DOMException('denied', 'SecurityError'); };
    expect(restoreQueryCache(new QueryClient(), blocked, 'build-1')).toBe(0);
    const { storage, values } = memoryStorage();
    values.set(QUERY_CACHE_KEY, '{not json');
    expect(restoreQueryCache(new QueryClient(), () => storage, 'build-1')).toBe(0);
    vi.restoreAllMocks();
  });

});
