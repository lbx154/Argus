import type { EventMsg, Snapshot } from '../types';

export interface ActiveWorkbenchPageProps {
  sid: string;
  snapshot: Snapshot;
  events: EventMsg[];
  refresh: () => void | Promise<void>;
  active: boolean;
}
