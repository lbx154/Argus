import type { Snapshot } from '../types';

export interface ActiveWorkbenchPageProps {
  sid: string;
  snapshot: Snapshot;
  refresh: () => void | Promise<void>;
  active: boolean;
}
