import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { Landing } from '../components/Landing';

vi.mock('../i18n', () => ({
  useI18n: () => ({ locale: 'en', t: (key: string) => key }),
}));

it('offers an empty workspace a direct start, four draft examples and the phone menu', () => {
  const html = renderToStaticMarkup(<Landing
    loading={false}
    hasProjects={false}
    canCreate
    onRetry={() => undefined}
    onNew={() => undefined}
    onChoose={() => undefined}
  />);
  expect(html).toContain('What would you like to accomplish?');
  expect(html).toContain('landing.new');
  expect(html.match(/<button /g)).toHaveLength(9);
  expect(html.match(/class="starter-card showcase-card"/g)).toHaveLength(3);
  expect(html).toContain('aria-label="topbar.openSessions"');
  expect(html).toContain('class="landing-navigation lg:hidden"');
  expect(html.match(/class="[^"]*landing-mark text-ink"/g)).toHaveLength(1);
  expect(html).toContain('width:var(--landing-mark-size);height:var(--landing-mark-size)');
  expect(html.indexOf('landing-mark text-ink')).toBeLessThan(html.indexOf('<h1'));
  expect(html).toContain('class="landing-intro"');
});

it('does not offer plugin management while the host is disconnected', () => {
  const html = renderToStaticMarkup(<Landing
    loading={false}
    hasProjects={false}
    canCreate={false}
    error="Host unavailable"
    onRetry={() => undefined}
    onNew={() => undefined}
    onChoose={() => undefined}
  />);
  expect(html).not.toContain('aria-label="Plugins"');
  expect(html).not.toContain('landing-mark text-ink');
});

it.each([
  { loading: true, hasProjects: false },
  { loading: false, hasProjects: true },
])('does not add a landing logo to the loading or existing-project state: %j', (state) => {
  const html = renderToStaticMarkup(<Landing
    {...state}
    canCreate
    onRetry={() => undefined}
    onNew={() => undefined}
    onChoose={() => undefined}
  />);
  expect(html).not.toContain('landing-mark text-ink');
  if (state.loading) expect(html.match(/data-logo="rounded-mark"/g)).toHaveLength(1);
});
