import { describe, expect, it, vi } from 'vitest';
import { installStaleChunkRecovery } from '../lib/preloadRecovery';

describe('stale frontend chunk recovery', () => {
  it('cancels preload failures and reloads the shell once', () => {
    const target = new EventTarget();
    const reload = vi.fn();
    installStaleChunkRecovery(target, reload);

    const first = new Event('vite:preloadError', { cancelable: true });
    const duplicate = new Event('vite:preloadError', { cancelable: true });

    expect(target.dispatchEvent(first)).toBe(false);
    expect(target.dispatchEvent(duplicate)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
