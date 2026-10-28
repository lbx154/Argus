import { createContext } from 'react';
import type { LiveLine as WorkLine } from '../lib/liveActivity';

/** What the running task is doing right now, for its card; provided once by
 * MapPanel so each poll re-renders only the line that reads it, never the cards. */
export const MapCardNowContext = createContext<{ taskId: string; said: WorkLine | null; running: WorkLine | null } | null>(null);

/** The figure each task's card shows (`taskFigures`), with the project to load it from. */
export const MapCardFigureContext = createContext<{ sid: string; figures: ReadonlyMap<string, string> }>({
  sid: '', figures: new Map(),
});
