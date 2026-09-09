import { describe, expect, it, vi } from 'vitest';
import { installStaleChunkRecovery } from '../lib/preloadRecovery';

function preloadError(message = 'Failed to fetch dynamically imported module: /assets/old.js') {
  return Object.assign(new Event('vite:preloadError', { cancelable: true }), { payload: new Error(message) });
}
function storage() {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
}

describe('stale frontend chunk recovery', () => {
  it('reloads once for a genuinely stale chunk', () => {
    const target = new EventTarget();
    const reload = vi.fn();
    installStaleChunkRecovery(target, reload, storage());
    expect(target.dispatchEvent(preloadError())).toBe(false);
    expect(target.dispatchEvent(preloadError())).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does not loop after the new document encounters the same broken download', () => {
    const saved = storage();
    const reload = vi.fn();
    for (const expected of [false, true]) {
      const document = new EventTarget();
      installStaleChunkRecovery(document, reload, saved);
      expect(document.dispatchEvent(preloadError())).toBe(expected);
    }
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does not hide runtime evaluation errors or reload on a frozen prototype defect', () => {
    const target = new EventTarget();
    const reload = vi.fn();
    installStaleChunkRecovery(target, reload, storage());
    expect(target.dispatchEvent(preloadError("Cannot assign to read only property 'constructor'"))).toBe(true);
    expect(reload).not.toHaveBeenCalled();
  });

  it('leaves failures visible when browser storage is blocked', () => {
    const target = new EventTarget();
    const reload = vi.fn();
    installStaleChunkRecovery(target, reload, { getItem: () => { throw new Error('blocked'); }, setItem: vi.fn() });
    expect(target.dispatchEvent(preloadError())).toBe(true);
    expect(reload).not.toHaveBeenCalled();
  });
});
