import { afterEach, describe, expect, test, vi } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nProvider, translate } from '../i18n';
import { ConnectionProblemBanner } from '../components/ConnectionProblemBanner';
import { PairingRequiredError } from '../api';
import { renderLine, type RenderContext } from '../../../core/src/eventRender';
import { roleLabel } from '../research-workbench/enumLabels';

const ZH: RenderContext = { locale: 'zh-CN', showReasoning: true, unknownEventPolicy: 'hide', density: 'compact' };
afterEach(() => vi.unstubAllGlobals());

test.each(['zh-CN', 'en'] as const)('guides a local installation to its terminal link in %s, even with an invalid token', locale => {
  vi.stubGlobal('localStorage', { getItem: (key: string) => key === 'argus.locale' ? locale : null });
  for (const search of ['', '?token=invalid']) {
    vi.stubGlobal('window', { location: { search } });
    const markup = renderToStaticMarkup(createElement(I18nProvider, null,
      createElement(ConnectionProblemBanner, { error: new PairingRequiredError(), onRetry: () => {} })));
    expect(markup).toContain(locale === 'zh-CN' ? '终端中 argus 打印的链接' : 'link printed by argus in your terminal');
    expect(markup).not.toMatch(/试用|邀请|trial|invited|sent you/i);
  }
});

describe('web localization', () => {
  test.each([
    ['Manager', '经理'], ['Planner', '规划者'], ['Engineer', '工程师'], ['Reviewer', '审阅者'],
  ])('localizes the %s role name across workbench surfaces', (role, chinese) => {
    for (const locale of ['zh-CN', 'en'] as const) {
      const expected = locale === 'zh-CN' ? chinese : role;
      expect(translate(`role.${role.toLowerCase()}`, {}, locale)).toBe(expected);
      expect(roleLabel(role.toLowerCase(), (zh, en) => locale === 'zh-CN' ? zh : en)).toBe(expected);
    }
  });
  test('translates interface messages with interpolation', () => {
    expect(translate('sidebar.manage', { name: 'demo' }, 'en')).toBe('Manage demo');
    expect(translate('sidebar.manage', { name: 'demo' }, 'zh-CN')).toBe('管理 demo');
  });

  test('localizes generated event status but preserves model text', () => {
    expect(renderLine({ type: 'round.start', round: 2 }, ZH)?.text).toBe('第 2 轮');
    expect(renderLine({
      type: 'engineer.progress',
      kind: 'assistant_message',
      text: 'Keep this model response unchanged.',
      agent_layer: 'engineer',
    }, ZH)?.text).toBe('Keep this model response unchanged.');
  });
});
