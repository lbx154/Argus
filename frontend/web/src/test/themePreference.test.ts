import { afterEach, describe, expect, it, vi } from 'vitest';

import { readThemeStyle, THEME_STYLE_STORAGE_KEY } from '../lib/themePreference';

describe('theme style preference', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('restores the gradient preference', () => {
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => key === THEME_STYLE_STORAGE_KEY ? 'gradient' : null,
    });

    expect(readThemeStyle()).toBe('gradient');
  });

  it('defaults unknown and unavailable preferences to standard', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => 'unknown',
    });
    expect(readThemeStyle()).toBe('standard');

    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked');
      },
    });
    expect(readThemeStyle()).toBe('standard');
  });
});
