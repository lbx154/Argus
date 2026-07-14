import { describe, expect, it } from 'vitest';
import { eventViewReducer, initialEventViewState } from '../lib/eventView';

describe('event view reconnect reset', () => {
  it('drops a stale clear offset when reconnect reseeds the stream', () => {
    const cleared = eventViewReducer(initialEventViewState, {
      kind: 'clear',
      offset: 12,
    });
    expect(cleared).toEqual({ skipFirst: 12, reconnectKey: 0 });

    const reconnected = eventViewReducer(cleared, {
      kind: 'reconnect',
    });
    expect(reconnected).toEqual({ skipFirst: 0, reconnectKey: 1 });
  });
});
