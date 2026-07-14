export interface EventViewState {
  skipFirst: number;
  reconnectKey: number;
}

export const initialEventViewState: EventViewState = {
  skipFirst: 0,
  reconnectKey: 0,
};

export type EventViewAction =
  | { kind: 'clear'; offset: number }
  | { kind: 'reconnect' }
  | { kind: 'reset' };

export function eventViewReducer(state: EventViewState, action: EventViewAction): EventViewState {
  if (action.kind === 'clear') {
    return {
      ...state,
      skipFirst: Math.max(0, action.offset),
    };
  }
  if (action.kind === 'reconnect') {
    return {
      skipFirst: 0,
      reconnectKey: state.reconnectKey + 1,
    };
  }
  return {
    ...state,
    skipFirst: 0,
  };
}
