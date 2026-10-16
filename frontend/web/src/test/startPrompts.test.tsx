import { act, create } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { StartPrompts } from '../components/StartPrompts';
import { I18nProvider } from '../i18n';

afterEach(() => vi.unstubAllGlobals());

describe('goal-oriented starter cards', () => {
  it.each(['zh-CN', 'en'])('prepares an editable prompt without executing anything (%s)', locale => {
    vi.stubGlobal('localStorage', { getItem: () => locale });
    vi.stubGlobal('document', { documentElement: { lang: '' } });
    const pick = vi.fn();
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(<I18nProvider><StartPrompts onPick={pick} /></I18nProvider>); });
    const buttons = renderer.root.findAllByType('button');
    expect(buttons).toHaveLength(4);
    expect(pick).not.toHaveBeenCalled();
    buttons.forEach(button => act(() => button.props.onClick()));
    expect(pick).toHaveBeenCalledTimes(4);
    expect(new Set(pick.mock.calls.map(([prompt]) => prompt)).size).toBe(4);
    const titles = renderer.root.findAllByType('strong').map(node => node.children.join(''));
    expect(titles).toEqual(locale === 'zh-CN'
      ? ['调研清楚，帮我做决策', '把想法做成可用成果', '找出问题，验证解决办法', '持续推进一个目标']
      : ['Research a decision', 'Turn an idea into a result', 'Diagnose and verify a fix', 'Advance a longer-term goal']);
    act(() => renderer.update(<I18nProvider><StartPrompts onPick={pick} disabled /></I18nProvider>));
    expect(renderer.root.findAllByType('button').every(button => button.props.disabled)).toBe(true);
    act(() => renderer.unmount());
  });
});
