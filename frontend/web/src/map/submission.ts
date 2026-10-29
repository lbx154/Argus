/** How the main composer restores a draft after a failed Manager request. */
export type MessageDispatch =
  | { type: 'settled'; outcome: 'message' | 'error' | 'cancelled' };
export type DispatchObserver = (event: MessageDispatch) => void;
/** ``whileRunning``: typed during a running reply; shown now and answered as the next Manager turn. */
export interface MapSendOptions { whileRunning?: boolean }
