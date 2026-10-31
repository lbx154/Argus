/** Plain native status data: safe to import without Tauri's privileged IPC. */
export type LaunchState = 'idle' | 'starting' | 'ready' | 'error' | 'stopped';

export interface DesktopStatus {
  state: LaunchState;
  message: string;
  detail?: string;
  warning?: string;
  pid?: number;
  url?: string;
}
