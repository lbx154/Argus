import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { Landing } from '../components/Landing';

vi.mock('../i18n', () => ({
  useI18n: () => ({ locale: 'en', t: (key: string) => key }),
}));

it('gives an empty workspace one primary action', () => {
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
  expect(html.match(/<button /g)).toHaveLength(1);
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
