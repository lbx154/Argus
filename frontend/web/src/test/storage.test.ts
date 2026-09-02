import { afterEach, describe, expect, it, vi } from 'vitest';

import { readLocalStorage, writeLocalStorage } from '../lib/storage';

describe('optional browser storage', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('falls back cleanly when storage access is blocked', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
    });

    expect(readLocalStorage('argus.theme')).toBeNull();
    expect(writeLocalStorage('argus.theme', 'dark')).toBe(false);
  });

  it('reads and writes preferences when storage is available', () => {
    const values = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    });

    expect(writeLocalStorage('argus.theme', 'dark')).toBe(true);
    expect(readLocalStorage('argus.theme')).toBe('dark');
  });
});
