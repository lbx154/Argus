import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceErrorBoundary } from '../components/WorkspaceErrorBoundary';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('workspace failure recovery', () => {
  it('shows explicit recovery instead of a blank page or automatic reload loop', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const reload = vi.fn();
    const assign = vi.fn();
    vi.stubGlobal('window', { location: { href: 'http://127.0.0.1:18799/?project=demo&view=map', reload, assign } });
    const Broken = (): never => { throw new Error('Lazy module evaluation failed'); };
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(createElement(WorkspaceErrorBoundary, { locale: 'zh-CN', children: createElement(Broken) })); });
    const buttons = renderer.root.findAllByType('button');
    expect(buttons.map((button) => button.children.join(''))).toEqual(['重新加载工作台', '返回对话页面']);
    expect(reload).not.toHaveBeenCalled();
    act(() => buttons[1].props.onClick());
    expect(assign).toHaveBeenCalledWith('http://127.0.0.1:18799/?project=demo&view=activity');
    act(() => buttons[0].props.onClick());
    expect(reload).toHaveBeenCalledTimes(1);
    act(() => renderer.unmount());
  });
});
