import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { Landing } from '../components/Landing';

vi.mock('../i18n', () => ({
  useI18n: () => ({ locale: 'en', t: (key: string) => key }),
}));

it('opens plugin discovery without requiring a placeholder Argus session', () => {
  const html = renderToStaticMarkup(<Landing
    loading={false}
    hasProjects={false}
    canCreate
    onRetry={() => undefined}
    onNew={() => undefined}
    onChoose={() => undefined}
  />);
  expect(html).toContain('aria-label="Plugins"');
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
