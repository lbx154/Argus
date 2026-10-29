import { createContext } from 'react';

/** The figure each task's card shows (`taskFigures`), with the project to load it from. */
export const MapCardFigureContext = createContext<{ sid: string; figures: ReadonlyMap<string, string> }>({
  sid: '', figures: new Map(),
});
