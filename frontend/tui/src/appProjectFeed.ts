import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import type { WebSocket } from 'ws';
import type { ApiClient, EventMsg, Snapshot } from './api.js';
import { reduceOperatorEvent } from '../../core/src/activity.js';
import { mergeTranscriptReplay } from './transcript.js';

const MAX_EVENTS = 400;
const STREAM_RENDER_INTERVAL_MS = 50;

export interface ProjectFeedState {
  snap: Snapshot | null;
  setSnap: Dispatch<SetStateAction<Snapshot | null>>;
  events: EventMsg[];
  setEvents: Dispatch<SetStateAction<EventMsg[]>>;
  connected: boolean;
  snapshotError: string;
  streamError: string;
  wsRef: MutableRefObject<WebSocket | null>;
  closeStream: () => void;
  shutdown: () => void;
}

export function useProjectFeed(api: ApiClient, project: string): ProjectFeedState {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<EventMsg[]>([]);
  const [connected, setConnected] = useState(false);
  const [snapshotError, setSnapshotError] = useState('');
  const [streamError, setStreamError] = useState('');
  const wsRef = useRef<WebSocket | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    setEvents([]);
    setSnap(null);
    setConnected(false);
    setSnapshotError('');
    setStreamError('');
  }, [project]);

  useEffect(() => {
    aliveRef.current = true;
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let renderTimer: ReturnType<typeof setTimeout> | undefined;
    let pendingEvents: EventMsg[] = [];
    const flushEvents = () => {
      renderTimer = undefined;
      if (!active || pendingEvents.length === 0) return;
      const batch = pendingEvents;
      pendingEvents = [];
      setEvents((prev) => (
        batch.reduce(
          (current, event) => reduceOperatorEvent(current, event, MAX_EVENTS),
          prev,
        )
      ));
    };
    const queueEvent = (event: EventMsg) => {
      if (!active) return;
      pendingEvents.push(event);
      if (!renderTimer) renderTimer = setTimeout(flushEvents, STREAM_RENDER_INTERVAL_MS);
    };
    const connect = () => {
      if (!active || !aliveRef.current) return;
      wsRef.current = api.connectStream({
        replay: 60,
        onOpen: () => {
          if (!active) return;
          setConnected(true);
          setStreamError('');
        },
        onEvent: queueEvent,
        onClose: () => {
          if (!active) return;
          flushEvents();
          setConnected(false);
          if (aliveRef.current) retry = setTimeout(connect, 1000);
        },
        onError: (error) => {
          if (active) setStreamError(error.message || 'event stream unavailable');
        },
      });
    };
    connect();
    return () => {
      active = false;
      if (retry) clearTimeout(retry);
      if (renderTimer) clearTimeout(renderTimer);
      pendingEvents = [];
      wsRef.current?.close();
    };
  }, [api]);

  useEffect(() => {
    let active = true;
    api.getTranscript(MAX_EVENTS).then(
      (turns) => {
        if (!active) return;
        setEvents((live) => mergeTranscriptReplay(live, turns, MAX_EVENTS));
      },
      () => {
        // Event streaming remains usable when an old project has no transcript.
      },
    );
    return () => {
      active = false;
    };
  }, [api]);

  useEffect(() => {
    let alive = true;
    let snapshotInFlight = false;
    const tick = async () => {
      if (snapshotInFlight) return;
      snapshotInFlight = true;
      try {
        const s = await api.snapshot();
        if (alive) {
          setSnap((current) => (
            current && JSON.stringify(current) === JSON.stringify(s)
              ? current
              : s
          ));
          setSnapshotError('');
        }
      } catch (error) {
        if (alive) setSnapshotError((error as Error).message || 'snapshot refresh failed');
      } finally {
        snapshotInFlight = false;
      }
    };
    tick();
    const id = setInterval(tick, 5_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [api]);

  const closeStream = () => {
    wsRef.current?.close();
  };

  const shutdown = () => {
    aliveRef.current = false;
    closeStream();
  };

  return {
    snap,
    setSnap,
    events,
    setEvents,
    connected,
    snapshotError,
    streamError,
    wsRef,
    closeStream,
    shutdown,
  };
}
