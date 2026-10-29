import type { EventMsg, Snapshot } from '../types';

export interface ActiveWorkbenchPageProps {
  sid: string;
  snapshot: Snapshot;
  events: EventMsg[];
  connected: boolean;
  refresh: () => void | Promise<void>;
  controls: {
    start: () => Promise<unknown>;
    stop: (drain: boolean) => Promise<unknown>;
    busy: boolean;
    error: string;
  };
  active: boolean;
}
