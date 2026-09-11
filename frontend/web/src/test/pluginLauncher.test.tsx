import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { PluginCard, PluginEntries, type Plugin } from '../components/PluginLauncher';
import { pluginEntryState } from '../lib/pluginLaunch';

const host = vi.hoisted(() => ({ locale: 'en' }));
vi.mock('../i18n', () => ({ useI18n: () => ({ locale: host.locale, t: (key: string) => key }) }));

const catalog: Plugin = {
  id: 'crystalpilot', name: 'Argus CrystalPilot', description: '衍射数据处理、结构求解、精修与三维晶体研究工作台',
  version: '0.4.0', url: '/plugins/crystalpilot/', command: '/crystalpilot', installed: false, enabled: false,
  supported: true, reason: '', update_available: false, backends: { default: 'pi', engineer: 'pi' },
  setup: { actions: ['health', 'repair', 'shelx', 'configure'] },
};
const ready: Plugin = { ...catalog, installed: true, enabled: true, installed_version: '0.4.0' };
const preparing: Plugin = { ...catalog, managed_by_host: true, operation: { status: 'running', progress: '安装独立运行环境（首次可能需要几分钟）' } };
const failed: Plugin = { ...catalog, managed_by_host: true, operation: { status: 'failed', error: '插件包校验失败，未安装。' } };

const entries = (plugins: Plugin[]) => renderToStaticMarkup(
  <PluginEntries plugins={plugins} pending={null} onLaunch={() => undefined} onManage={() => undefined} />,
);
const card = (plugin: Plugin) => renderToStaticMarkup(
  <PluginCard plugin={plugin} running={plugin.operation?.status === 'running'} locale={host.locale} act={async () => true} />,
);

describe('workbench entries outside the plugin center', () => {
  it('opens an installed workbench directly and offers no install control', () => {
    const html = entries([ready]);
    expect(html).toContain('aria-label="Argus CrystalPilot"');
    expect(html).toContain('aria-label="Manage Argus CrystalPilot"');
    expect(html).not.toMatch(/>Install</);
    expect(html).not.toContain('aria-label="Plugins"');
    expect(html).not.toContain('role="status"');
  });

  it('shows one plain preparing sentence with the current step while the service installs', () => {
    const html = entries([preparing]);
    expect(html).toContain('role="status"');
    expect(html).toContain('Preparing the Argus CrystalPilot workbench; the first time takes a few minutes');
    expect(html).toContain('Install standalone environment (may take several minutes the first time)');
    expect(html).not.toMatch(/>Install</);
    expect(html).not.toContain('aria-label="Plugins"');
    expect(html).not.toContain('aria-label="Argus CrystalPilot"');
  });

  it('says plainly when a provided workbench could not be prepared', () => {
    const html = entries([failed]);
    expect(html).toContain('Argus CrystalPilot is unavailable for now');
    expect(html).not.toMatch(/>Install</);
  });

  it('keeps the plugin center as the manual path for a self-hosted copy', () => {
    expect(entries([catalog])).toContain('aria-label="Plugins"');
    expect(entries([catalog])).not.toContain('role="status"');
    expect(entries([])).toContain('aria-label="Plugins"');
    expect(entries([ready, { ...catalog, id: 'other', name: 'Other' }])).toContain('aria-label="Plugins"');
  });

  it('renders only the icon in the slim sidebar and speaks the host language', () => {
    host.locale = 'zh-CN';
    try {
      const slim = renderToStaticMarkup(<PluginEntries plugins={[preparing]} compact pending={null} onLaunch={() => undefined} onManage={() => undefined} />);
      expect(slim).toContain('title="安装独立运行环境（首次可能需要几分钟）"');
      expect(slim).not.toContain('正在准备 Argus CrystalPilot 工作台');
      expect(entries([preparing])).toContain('正在准备 Argus CrystalPilot 工作台，首次需要几分钟');
      expect(entries([ready])).toContain('aria-label="管理 Argus CrystalPilot"');
    } finally {
      host.locale = 'en';
    }
  });
});

describe('plugin center cards', () => {
  it('hides disable, uninstall and update for a plugin the service provides', () => {
    const html = card({ ...ready, managed_by_host: true, update_available: true });
    expect(html).toContain('Open workbench');
    expect(html).toContain('Check environment');
    expect(html).not.toMatch(/>Disable<|>Uninstall<|>Update to /);
    expect(html).not.toContain('Uninstalling preserves');
  });

  it('keeps every manual control for a self-hosted copy', () => {
    const html = card({ ...ready, update_available: true });
    expect(html).toMatch(/>Disable</);
    expect(html).toMatch(/>Uninstall</);
    expect(html).toMatch(/>Update to /);
    expect(card(catalog)).toMatch(/>Install</);
  });

  it('replaces the install button with the preparing sentence for a provided plugin', () => {
    const html = card(preparing);
    expect(html).toContain('Preparing the Argus CrystalPilot workbench');
    expect(html).toContain('Install standalone environment');
    expect(html).not.toMatch(/>Install</);
    expect(html).not.toContain('First installation automatically configures');
  });
});

describe('entry state', () => {
  it('derives one state per plugin', () => {
    expect(pluginEntryState(ready)).toBe('ready');
    expect(pluginEntryState({ ...ready, managed_by_host: true })).toBe('ready');
    expect(pluginEntryState(catalog)).toBe('manual');
    expect(pluginEntryState({ ...ready, enabled: false })).toBe('manual');
    expect(pluginEntryState({ ...catalog, managed_by_host: true })).toBe('preparing');
    expect(pluginEntryState(preparing)).toBe('preparing');
    expect(pluginEntryState({ ...ready, enabled: false, managed_by_host: true })).toBe('preparing');
    expect(pluginEntryState(failed)).toBe('unavailable');
    expect(pluginEntryState({ ...ready, managed_by_host: true, supported: false })).toBe('unavailable');
  });
});
