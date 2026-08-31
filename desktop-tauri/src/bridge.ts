import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';

export type RunnerKind =
  | 'codex'
  | 'claude'
  | 'copilot'
  | 'pi'
  | 'opencode'
  | 'grok'
  | 'qoder'
  | 'dsh';
export type AppearanceTheme = 'system' | 'light' | 'dark';
export type LaunchState = 'idle' | 'starting' | 'ready' | 'error' | 'stopped';

export interface DesktopStatus {
  state: LaunchState;
  message: string;
  detail?: string;
  pid?: number;
  url?: string;
}

export interface PiConfiguration {
  configDir: string;
  provider?: string;
  model?: string;
  qualifiedModel?: string;
}

export interface DesktopReleaseIdentity {
  packageVersion: string;
  releaseId: string;
  sourceDigest: string;
  distribution: 'development' | 'packaged';
}

export interface DesktopRuntimeIdentity {
  state: LaunchState;
  pid?: number;
  url?: string;
}

export interface DesktopSetup {
  complete: boolean;
  host: string;
  port: number;
  runnerKind: RunnerKind;
  runnerBins: Partial<Record<RunnerKind, string>>;
  runnerConfigured: boolean;
  detectedRunners: Partial<Record<RunnerKind, string>>;
  piConfiguration: PiConfiguration;
  releaseIdentity: DesktopReleaseIdentity;
  runtimeIdentity: DesktopRuntimeIdentity;
}

export interface DesktopAppearance {
  theme: AppearanceTheme;
  resolvedTheme: 'light' | 'dark';
}

export interface SetupResult {
  ok: boolean;
  error?: string;
}

export interface DesktopDeliveryNotification {
  deliveryId: string;
  title: string;
  summary: string;
  path?: string;
}

export type UpdateState =
  | 'idle'
  | 'checking'
  | 'up-to-date'
  | 'available'
  | 'downloading'
  | 'installing'
  | 'error';

export interface UpdateStatus {
  state: UpdateState;
  currentVersion: string;
  /** Only explicit user actions may surface non-update check feedback. */
  userInitiated: boolean;
  availableVersion?: string;
  notes?: string;
  progress?: number;
  detail?: string;
}

function eventSubscription<T>(
  name: string,
  callback: (payload: T) => void,
): () => void {
  let disposed = false;
  let unlisten: UnlistenFn | undefined;
  void listen<T>(name, (event) => {
    if (!disposed) callback(event.payload);
  }).then((release) => {
    if (disposed) release();
    else unlisten = release;
  }).catch(() => undefined);
  return () => {
    disposed = true;
    unlisten?.();
  };
}

export const desktopBridge = {
  getStatus: (): Promise<DesktopStatus> => invoke('get_status'),
  onStatus: (callback: (status: DesktopStatus) => void): (() => void) =>
    eventSubscription('argus:status', callback),
  getSetup: (): Promise<DesktopSetup> => invoke('get_setup'),
  getAppearance: (): Promise<DesktopAppearance> => invoke('get_appearance'),
  setAppearance: (appearance: { theme: 'light' | 'dark' }): Promise<DesktopAppearance> =>
    invoke('set_appearance', { input: appearance }),
  setWindowTheme: (theme: AppearanceTheme): Promise<void> =>
    invoke('set_window_theme', { theme }),
  setLargePreview: (active: boolean): Promise<void> =>
    invoke('set_large_preview', { active }),
  chooseRunner: (kind: RunnerKind): Promise<string | null> =>
    invoke('choose_runner', { kind }),
  completeSetup: (input: {
    port: number;
    runnerKind: RunnerKind;
    runnerBins: Partial<Record<RunnerKind, string>>;
  }): Promise<SetupResult> => invoke('complete_setup', { input }),
  hideDesktop: (): Promise<void> => invoke('hide_desktop'),
  stopBackendAndQuit: (): Promise<void> => invoke('stop_backend_and_quit'),
  showAbout: (): Promise<void> => invoke('show_about'),
  openLogs: (): Promise<string> => invoke('open_logs'),
  openData: (): Promise<string> => invoke('open_data'),
  restartBackend: (): Promise<boolean> => invoke('restart_backend'),
  exportDiagnostics: (): Promise<string | null> => invoke('export_diagnostics'),
  openCockpit: (): Promise<string> => invoke('open_cockpit'),
  openExternal: (url: string): Promise<void> => invoke('open_external', { url }),
  notifyDelivery: (payload: DesktopDeliveryNotification): Promise<boolean> =>
    invoke('notify_delivery', { input: payload }),
  onOpenDelivery: (
    callback: (payload: DesktopDeliveryNotification) => void,
  ): (() => void) => eventSubscription('argus:open-delivery', callback),
  onShowSetup: (callback: () => void): (() => void) =>
    eventSubscription('argus:show-setup', callback),
  onNewChat: (callback: () => void): (() => void) =>
    eventSubscription('argus:new-chat', callback),
  getUpdateStatus: (): Promise<UpdateStatus> => invoke('get_update_status'),
  checkForUpdate: (): Promise<UpdateStatus> => invoke('check_for_update', { manual: true }),
  installUpdate: (): Promise<void> => invoke('install_update'),
  dismissUpdate: (): Promise<void> => invoke('dismiss_update'),
  onUpdateStatus: (callback: (status: UpdateStatus) => void): (() => void) =>
    eventSubscription('argus:update-status', callback),
};
