/** Lifecycle of one actual Manager request; animation never guesses a task identity. */
export type MessageDispatch =
  | { type: 'task'; taskId: string }
  | { type: 'settled'; outcome: 'message' | 'error' | 'cancelled' };
export type DispatchObserver = (event: MessageDispatch) => void;
/** ``whileRunning``: typed during a running turn; the backend decides whether it steers that turn or waits for the next. */
export interface MapSendOptions { whileRunning?: boolean }
export type MapSend = (text: string, files?: File[], observe?: DispatchObserver, options?: MapSendOptions) => Promise<boolean>;
