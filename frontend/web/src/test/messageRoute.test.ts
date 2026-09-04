import { afterEach, describe, expect, it, vi } from 'vitest';

import { initialMessageRoute, MESSAGE_ROUTE_KEY } from '../lib/messageRoute';

describe('initialMessageRoute', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('defaults an embedded Desktop cockpit to Auto', () => {
    vi.stubGlobal('window', { parent: {} });
    vi.stubGlobal('localStorage', { getItem: () => null });

    expect(initialMessageRoute()).toBe('auto');
  });

  it('ignores the legacy implicit Task preference', () => {
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => key === 'argus.message.route.v1' ? 'task' : null,
    });

    expect(MESSAGE_ROUTE_KEY).toBe('argus.message.route.v2');
    expect(initialMessageRoute()).toBe('auto');
  });

  it('preserves a new explicit operator preference', () => {
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => key === MESSAGE_ROUTE_KEY ? 'chat' : null,
    });

    expect(initialMessageRoute()).toBe('chat');
  });
});
