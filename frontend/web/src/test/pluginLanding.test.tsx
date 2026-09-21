import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { Landing } from '../components/Landing';

vi.mock('../i18n', () => ({
  useI18n: () => ({ locale: 'en', t: (key: string) => key }),
}));

it('gives an empty workspace one primary action plus the phone menu', () => {
  const html = renderToStaticMarkup(<Landing
    loading={false}
    hasProjects={false}
    canCreate
    onRetry={() => undefined}
    onNew={() => undefined}
    onChoose={() => undefined}
  />);
  expect(html).toContain('Start a project');
  expect(html).toContain('landing.new');
  // One primary action; the second button is the phone-only menu (hidden at lg).
  expect(html.match(/<button /g)).toHaveLength(2);
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
