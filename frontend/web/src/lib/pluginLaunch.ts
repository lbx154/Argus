// How a plugin shows up outside the plugin center: as a workbench entry that
// opens directly, as a quiet preparing line while the service installs it,
// or, for self-hosted copies, through the plugin center where Install lives.
export type PluginEntry = {
  id: string; name: string; installed: boolean; enabled: boolean; supported: boolean;
  managed_by_host?: boolean; reason?: string;
  operation?: { status?: string; progress?: string; error?: string };
};

export type PluginEntryState = 'ready' | 'preparing' | 'unavailable' | 'manual';

export function pluginEntryState(plugin: PluginEntry): PluginEntryState {
  if (plugin.installed && plugin.enabled && plugin.supported) return 'ready';
  if (!plugin.managed_by_host) return 'manual';
  if (!plugin.supported || plugin.operation?.status === 'failed') return 'unavailable';
  return 'preparing';
}

export const preparingSentence = (name: string) => `正在准备 ${name} 工作台，首次需要几分钟`;
export const unavailableSentence = (name: string) => `${name} 暂时无法使用`;
