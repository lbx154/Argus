import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MobileTabBar } from '../components/MobileTabBar';

describe('MobileTabBar', () => {
  const markup = (active: 'mission' | 'activity' | 'workbench' | 'preview' = 'activity') =>
    renderToStaticMarkup(
      <MobileTabBar active={active} onSelect={() => {}} onOpenSessions={() => {}} />,
    );

  it('offers every destination that is otherwise reachable only on desktop', () => {
    const html = markup();

    for (const label of ['Sessions', 'Mission', 'Activity', 'Workbench', 'Preview']) {
      expect(html).toContain(`>${label}<`);
    }
  });

  it('marks the active destination for assistive tech', () => {
    // The tab that is current carries aria-current; exactly one does.
    expect(markup('preview').match(/aria-current="page"/g)).toHaveLength(1);
  });

  it('keeps every target at or above the touch-size minimum', () => {
    const html = markup();

    // 3.25rem = 52px, past Apple's 44pt and Material's 48dp.
    expect(html.match(/min-h-\[3\.25rem\]/g)).toHaveLength(6);
  });

  it('respects the safe area and rides above the keyboard', () => {
    // .mobile-tabbar carries the safe-area padding and the keyboard offset.
    expect(markup()).toContain('mobile-tabbar');
  });

  it('hides itself where the full three-pane layout takes over', () => {
    expect(markup()).toContain('lg:hidden');
  });

  it('omits the session button when no handler is given', () => {
    const html = renderToStaticMarkup(
      <MobileTabBar active="activity" onSelect={() => {}} />,
    );

    expect(html).not.toContain('>Sessions<');
    expect(html.match(/min-h-\[3\.25rem\]/g)).toHaveLength(5);
  });
});

describe('QR pairing token adoption', () => {
  let stored: Record<string, string>;
  let replaced: string[];

  function stubBrowser(search: string, hash = '') {
    stored = {};
    replaced = [];
    vi.stubGlobal('window', {
      location: { search, pathname: '/', hash },
      history: {
        replaceState: (_s: unknown, _t: string, url: string) => replaced.push(url),
      },
    });
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored[key] ?? null,
      setItem: (key: string, value: string) => {
        stored[key] = value;
      },
    });
  }

  beforeEach(() => vi.resetModules());
  afterEach(() => vi.unstubAllGlobals());

  it('persists a token handed over in the URL', async () => {
    stubBrowser('?token=abc123');
    const { adoptTokenFromUrl } = await import('../api');

    adoptTokenFromUrl();

    // Without this the token would last only as long as the query string —
    // a reload, or launching the installed PWA from its start_url, would
    // land unauthenticated.
    expect(stored.argus_web_token).toBe('abc123');
  });

  it('clears the token from the address bar once stored', async () => {
    stubBrowser('?token=abc123');
    const { adoptTokenFromUrl } = await import('../api');

    adoptTokenFromUrl();

    expect(replaced).toEqual(['/']);
  });

  it('preserves other query parameters', async () => {
    stubBrowser('?kiosk=1&token=abc123');
    const { adoptTokenFromUrl } = await import('../api');

    adoptTokenFromUrl();

    expect(replaced).toEqual(['/?kiosk=1']);
    expect(stored.argus_web_token).toBe('abc123');
  });

  it('keeps the fragment intact', async () => {
    stubBrowser('?token=abc123', '#panel');
    const { adoptTokenFromUrl } = await import('../api');

    adoptTokenFromUrl();

    expect(replaced).toEqual(['/#panel']);
  });

  it('does nothing when the URL carries no token', async () => {
    stubBrowser('');
    const { adoptTokenFromUrl } = await import('../api');

    adoptTokenFromUrl();

    expect(replaced).toEqual([]);
    expect(stored.argus_web_token).toBeUndefined();
  });

  it('keeps authenticated requests working when storage is unavailable', async () => {
    stubBrowser('?token=abc123');
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => {
        throw new Error('private mode');
      },
    });
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      sid: 'demo',
      rc: 0,
      daemon: {},
      objective: '',
      workdir: '',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const { adoptTokenFromUrl, api } = await import('../api');

    expect(() => adoptTokenFromUrl()).not.toThrow();
    await api.createDaemon('');

    expect(fetchMock).toHaveBeenCalledWith('/api/daemons', expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer abc123' }),
    }));
  });
});
