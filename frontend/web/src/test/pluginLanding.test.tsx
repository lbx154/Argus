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
  expect(html.match(/<button /g)).toHaveLength(6);
  expect(html).toContain('does not start work automatically');
  expect(html).toContain('landing.menu');
  expect(html).toContain('class="lg:hidden"');
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
});
