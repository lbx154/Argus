import { createElement, type ReactNode } from 'react';
import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { PluginEnvironment, type PluginSetup } from '../components/PluginEnvironment';

vi.mock('../lib/pluginText', () => ({ usePluginText: () => (value: ReactNode) => value }));

const setup: PluginSetup = {
  actions: ['health', 'repair', 'configure'],
  windows_runtime: {
    action: 'platon_runtime', name: 'PLATON', url: 'https://example.invalid/official',
    notice: 'Official license terms; private directory only.',
  },
};

describe('official PLATON environment consent', () => {
  it('does not submit before explicit consent and clears the form after submission', async () => {
    const submit = vi.fn(async () => true);
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(createElement(PluginEnvironment, { setup, running: false, act: submit, platform: 'windows' })); });
    const button = (label: string) => renderer.root.findAllByType('button').find(node => node.children.includes(label))!;
    act(() => button('准备 PLATON 官方环境').props.onClick());
    expect(button('下载、验证并配置').props.disabled).toBe(true);
    await act(async () => { await renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
    expect(submit).not.toHaveBeenCalled();
    act(() => renderer.root.findByType('input').props.onChange({ target: { checked: true } }));
    expect(button('下载、验证并配置').props.disabled).toBe(false);
    await act(async () => { await renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
    expect(submit).toHaveBeenCalledExactlyOnceWith('platon_runtime', { accept_software_license: true });
    expect(renderer.root.findAllByType('form')).toHaveLength(0);
    act(() => renderer.unmount());
  });

  it('does not offer the host adapter when the server does not advertise it', () => {
    let renderer!: ReturnType<typeof create>;
    act(() => { renderer = create(createElement(PluginEnvironment, { setup: { actions: [] }, running: false, act: vi.fn() })); });
    expect(renderer.root.findAllByType('button').some(node => node.children.includes('准备 PLATON 官方环境'))).toBe(false);
    act(() => renderer.unmount());
  });
});
