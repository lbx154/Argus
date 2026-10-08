/** Lifecycle of one actual Manager request; animation never guesses a task identity. */
export type MessageDispatch =
  | { type: 'task'; taskId: string }
  | { type: 'settled'; outcome: 'message' | 'error' | 'cancelled' };
export type DispatchObserver = (event: MessageDispatch) => void;
/** ``whileRunning``: typed during a running reply; shown now and answered as the next Manager turn. */
export interface MapSendOptions { whileRunning?: boolean }
export type MapSend = (text: string, files?: File[], observe?: DispatchObserver, options?: MapSendOptions) => Promise<boolean>;
