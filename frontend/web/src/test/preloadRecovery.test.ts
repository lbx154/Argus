import { describe, expect, it, vi } from 'vitest';
import { installStaleChunkRecovery } from '../lib/preloadRecovery';

function failure(message: string) {
  return Object.assign(new Event('vite:preloadError', { cancelable: true }), {
    payload: new TypeError(message),
  });
}

function persistence() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
}

describe('stale frontend chunk recovery', () => {
  it('lets PDF loading and evaluation errors reach the local preview', () => {
    const target = new EventTarget();
    const reload = vi.fn();
    installStaleChunkRecovery(target, reload, { releaseId: 'release-1', storage: persistence });
    for (const message of [
      'Failed to fetch dynamically imported module: https://example.test/assets/pdf-abc123.js',
      'Failed to fetch dynamically imported module: https://example.test/assets/pdf.worker.min-abc123.mjs',
      'Promise.withResolvers is not a function',
    ]) {
      expect(target.dispatchEvent(failure(message))).toBe(true);
    }
    expect(reload).not.toHaveBeenCalled();
  });

  it('reloads a missing app chunk at most once, including after navigation', () => {
    const storage = persistence();
    const options = { releaseId: 'release-1', storage: () => storage };
    const reload = vi.fn();
    const target = new EventTarget();
    installStaleChunkRecovery(target, reload, options);
    const message = 'Failed to fetch dynamically imported module: https://example.test/assets/MapPanel-abc.js';
    expect(target.dispatchEvent(failure(message))).toBe(false);
    expect(target.dispatchEvent(failure(message))).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
    const afterNavigation = new EventTarget();
    installStaleChunkRecovery(afterNavigation, reload, options);
    expect(afterNavigation.dispatchEvent(failure(message))).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does not auto-reload when navigation history cannot be persisted', () => {
    const target = new EventTarget();
    const reload = vi.fn();
    installStaleChunkRecovery(target, reload, {
      releaseId: 'release-1', storage: () => { throw new Error('storage unavailable'); },
    });
    expect(target.dispatchEvent(failure('Failed to fetch dynamically imported module: /assets/MapPanel-old.js'))).toBe(true);
    expect(reload).not.toHaveBeenCalled();
  });
});
