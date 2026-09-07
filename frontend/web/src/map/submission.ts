/** Lifecycle of one actual Manager request; animation never guesses a task identity. */
export type MessageDispatch =
  | { type: 'task'; taskId: string }
  | { type: 'settled'; outcome: 'message' | 'error' | 'cancelled' };
export type DispatchObserver = (event: MessageDispatch) => void;
export type MapSend = (text: string, files?: File[], observe?: DispatchObserver) => Promise<boolean>;
