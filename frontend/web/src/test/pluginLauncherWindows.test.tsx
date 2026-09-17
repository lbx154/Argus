import { createElement, type ReactNode } from 'react';
import { act, create, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PluginLauncher } from '../components/PluginLauncher';

vi.mock('react-dom', () => ({ createPortal: (value: ReactNode) => value }));
vi.mock('../lib/pluginText', () => ({ usePluginText: () => (value: ReactNode) => value }));
vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'zh-CN' }) }));
vi.mock('../api', () => ({ authHeaders: () => ({}) }));

let renderer: ReturnType<typeof create> | undefined;
afterEach(() => {
  if (renderer) act(() => renderer!.unmount());
  renderer = undefined;
  vi.unstubAllGlobals();
});

const text = (node: ReactTestInstance | string): string => typeof node === 'string'
  ? node : node.children.map(text).join('');
const button = (label: string) => renderer!.root.findAllByType('button').find(node =>
  node.props['aria-label'] === label || text(node).trim() === label)!;
const manage = () => button('管理 CrystalPilot') || button('插件');
const posts = (fetch: ReturnType<typeof vi.fn>) => fetch.mock.calls.filter(([, options]) => options?.method === 'POST');

async function openPlugin({ installed = true, update = false, accepted = false } = {}) {
  const listeners = new Map<string, (event: unknown) => void>();
  vi.stubGlobal('document', { body: {} });
  vi.stubGlobal('window', {
    setInterval: vi.fn(() => 1), clearInterval: vi.fn(),
    addEventListener: (name: string, callback: (event: unknown) => void) => listeners.set(name, callback),
    removeEventListener: (name: string) => listeners.delete(name),
    location: { assign: vi.fn() },
  });
  const plugin = {
    id: 'crystalpilot', name: 'CrystalPilot', description: 'Scientific workbench',
    version: '0.4.0', installed_version: installed ? '0.3.0' : undefined,
    installed, enabled: installed, supported: true, reason: '', update_available: update,
    url: '/plugins/crystalpilot/', backends: { default: 'codex' },
    setup: { actions: ['health', 'repair', 'configure'], windows_runtime: {
      action: 'platon_runtime', name: 'PLATON', url: 'https://example.invalid/platon',
      notice: 'Official PLATON license', accepted,
    } },
    health: { checked: 1, ready: false, components: [{
      id: 'platon', name: 'PLATON', status: 'missing', detail: 'Needs repair',
      description: 'Validation', url: 'https://example.invalid/platon', automatic: true, license_required: false,
    }] },
  };
  const fetch = vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      plugin.setup.windows_runtime.accepted = true;
      return { ok: true, json: async () => ({ status: 'running' }) };
    }
    return { ok: true, json: async () => ({ plugins: [plugin] }) };
  });
  vi.stubGlobal('fetch', fetch);
  act(() => { renderer = create(createElement(PluginLauncher), { createNodeMock: () => ({ focus() {} }) }); });
  await act(async () => { manage().props.onClick(); });
  return { fetch, listeners };
}

describe('normal installation and repair consent', () => {
  for (const [action, label] of [['install', '安装'], ['update', '更新至 0.4.0'], ['repair', '修复依赖']] as const) {
    it(`${action} confirms the license and resumes the same action, not the special button`, async () => {
      const { fetch } = await openPlugin({ installed: action !== 'install', update: action === 'update' });
      await act(async () => { button(label).props.onClick(); });
      expect(posts(fetch)).toHaveLength(0);
      expect(button('同意并继续').props.disabled).toBe(true);
      await act(async () => { await renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
      expect(posts(fetch)).toHaveLength(0);
      act(() => renderer!.root.findByType('input').props.onChange({ target: { checked: true } }));
      await act(async () => { await renderer!.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
      const requests = posts(fetch);
      expect(requests).toHaveLength(1);
      expect(requests[0][0]).toBe(`/api/plugins/crystalpilot/manage/${action}`);
      expect(JSON.parse(requests[0][1].body)).toEqual({ accept_software_license: true });
      expect(renderer!.root.findAllByType('form')).toHaveLength(0);
    });
  }

  it('cancel does not submit and does not retain a checked consent', async () => {
    const { fetch } = await openPlugin();
    await act(async () => { button('修复依赖').props.onClick(); });
    act(() => renderer!.root.findByType('input').props.onChange({ target: { checked: true } }));
    act(() => button('取消').props.onClick());
    expect(renderer!.root.findAllByType('form')).toHaveLength(0);
    await act(async () => { button('修复依赖').props.onClick(); });
    expect(renderer!.root.findByType('input').props.checked).toBe(false);
    expect(posts(fetch)).toHaveLength(0);
  });

  for (const close of ['button', 'escape'] as const) {
    it(`closing via ${close} clears the pending action and its consent`, async () => {
      const { fetch, listeners } = await openPlugin();
      await act(async () => { button('修复依赖').props.onClick(); });
      act(() => renderer!.root.findByType('input').props.onChange({ target: { checked: true } }));
      act(() => {
        if (close === 'button') button('关闭插件列表').props.onClick();
        else listeners.get('keydown')!({ key: 'Escape' });
      });
      await act(async () => { manage().props.onClick(); });
      expect(renderer!.root.findAllByType('form')).toHaveLength(0);
      await act(async () => { button('修复依赖').props.onClick(); });
      expect(renderer!.root.findByType('input').props.checked).toBe(false);
      expect(posts(fetch)).toHaveLength(0);
    });
  }

  it('previously recorded consent allows ordinary repair without another dialog', async () => {
    const { fetch } = await openPlugin({ accepted: true });
    await act(async () => { button('修复依赖').props.onClick(); });
    expect(renderer!.root.findAllByType('form')).toHaveLength(0);
    expect(posts(fetch)).toHaveLength(1);
    expect(posts(fetch)[0][0]).toBe('/api/plugins/crystalpilot/manage/repair');
  });
});
