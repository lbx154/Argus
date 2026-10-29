import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { AgentActivity } from '../components/AgentActivity';
import { I18nProvider } from '../i18n';

let renderer: ReactTestRenderer | undefined;
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const textOf = (node: { children: unknown[] }): string => node.children
  .map(child => typeof child === 'string' ? child : textOf(child as { children: unknown[] })).join('');

it.each([
  ['zh-CN', '完整工作记录下载失败。请检查网络连接，恢复后再次点击下载；如果仍然失败，请查看错误详情。', '错误详情（原始信息）'],
  ['en', 'The full work log could not be downloaded. Check your connection, then click Download again; if it still fails, check the error details.', 'Error details (raw information)'],
])('localizes a failed full-history download in %s, folds the raw error and allows retry', async (locale, expected, detailsLabel) => {
  // Chromium's exception when downloading this log offline.
  const download = vi.spyOn(api, 'downloadWorkLog')
    .mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce(undefined);
  vi.stubGlobal('localStorage', { getItem: () => locale });
  vi.stubGlobal('document', { documentElement: { lang: '' } });
  await act(async () => {
    renderer = create(<I18nProvider><AgentActivity sid="s-fixture" selectedRole="engineer" taskId="current" /></I18nProvider>);
  });
  const button = () => renderer!.root.findAllByType('button').find(node => /下载完整工作记录|Download full work log/.test(textOf(node)))!;
  await act(async () => { button().props.onClick(); });
  expect(download).toHaveBeenLastCalledWith('s-fixture', 'engineer', 'current');
  const alert = renderer!.root.findByProps({ role: 'alert' });
  expect(textOf(alert).startsWith(expected)).toBe(true);
  const details = alert.findByType('details');
  expect(details.props.open).not.toBe(true);
  expect(textOf(details.findByType('summary'))).toBe(detailsLabel);
  expect(textOf(details.findByType('pre'))).toBe('Failed to fetch');
  expect(button().props.disabled).toBe(false);

  await act(async () => { button().props.onClick(); });
  expect(download).toHaveBeenCalledTimes(2);
  expect(download).toHaveBeenLastCalledWith('s-fixture', 'engineer', 'current');
  expect(renderer!.root.findAllByProps({ role: 'alert' })).toHaveLength(0);
  expect(button().props.disabled).toBe(false);
});
