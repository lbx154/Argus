import { describe, expect, test } from 'vitest';
import { COMMANDS } from '../../../core/src/commands';
import { translate } from '../i18n';
import { commandDescription, commandGroup } from '../lib/commandI18n';
import { renderLine, type RenderContext } from '../../../core/src/eventRender';
import { roleLabel } from '../research-workbench/enumLabels';

const ZH: RenderContext = { locale: 'zh-CN', showReasoning: true, unknownEventPolicy: 'hide', density: 'compact' };

describe('web localization', () => {
  test.each([
    ['Manager', '统筹'], ['Planner', '规划'], ['Engineer', '执行'], ['Reviewer', '复核'],
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

  test('localizes shared command presentation without changing command tokens', () => {
    const status = COMMANDS.find((command) => command.id === 'status')!;
    expect(status.name).toBe('/status');
    expect(commandDescription(status, 'zh-CN')).toContain('健康状态');
    expect(commandGroup(status, 'zh-CN')).toBe('常用');
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
