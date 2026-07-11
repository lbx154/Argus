import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useReducer, useRef, useState } from 'react';
import { api, openStream, type EventMsg } from './api';
import { eventKey } from './lib/eventRender';

/* ------------------------------------------------------------------ REST */

export const useProjects = () =>
  useQuery({ queryKey: ['projects'], queryFn: api.listProjects, refetchInterval: 5_000 });

export const useSnapshot = (sid: string | null) =>
  useQuery({
    queryKey: ['snapshot', sid],
    queryFn: () => api.snapshot(sid!),
    enabled: !!sid,
    refetchInterval: 4_000,
  });

export const useStatus = (sid: string | null, enabled = true) =>
  useQuery({
    queryKey: ['status', sid],
    queryFn: () => api.status(sid!),
    enabled: !!sid && enabled,
    refetchInterval: enabled ? 6_000 : false,
  });

export const useJournal = (sid: string | null, n = 30, enabled = true) =>
  useQuery({
    queryKey: ['journal', sid, n],
    queryFn: () => api.journal(sid!, n),
    enabled: !!sid && enabled,
    refetchInterval: enabled ? 8_000 : false,
  });

export const useDoctor = (sid: string | null, enabled: boolean) =>
  useQuery({ queryKey: ['doctor', sid], queryFn: () => api.doctor(sid!), enabled: !!sid && enabled });

export const useConfig = (sid: string | null, enabled: boolean) =>
  useQuery({ queryKey: ['config', sid], queryFn: () => api.config(sid!), enabled: !!sid && enabled });

export const useIdentity = (sid: string | null, enabled: boolean) =>
  useQuery({ queryKey: ['identity', sid], queryFn: () => api.identity(sid!), enabled: !!sid && enabled });

export const useTranscript = (sid: string | null, enabled: boolean, n = 30) =>
  useQuery({ queryKey: ['transcript', sid, n], queryFn: () => api.transcript(sid!, n), enabled: !!sid && enabled });

export const useArtifacts = (sid: string | null, enabled = true) =>
  useQuery({
    queryKey: ['artifacts', sid],
    queryFn: () => api.artifacts(sid!),
    enabled: !!sid && enabled,
    refetchInterval: enabled ? 8_000 : false,
  });

export const useArtifact = (sid: string | null, path: string | null) =>
  useQuery({
    queryKey: ['artifact', sid, path],
    queryFn: () => api.artifact(sid!, path!),
    enabled: !!sid && !!path,
  });

export const useBacklogItem = (sid: string | null, itemId: string | null) =>
  useQuery({
    queryKey: ['backlog-item', sid, itemId],
    queryFn: () => api.backlogItem(sid!, itemId!),
    enabled: !!sid && !!itemId,
  });

/* --------------------------------------------------------------- mutations */

export function useProjectActions(sid: string | null, commandRevision?: number) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['snapshot', sid] });
    qc.invalidateQueries({ queryKey: ['status', sid] });
    qc.invalidateQueries({ queryKey: ['projects'] });
    qc.invalidateQueries({ queryKey: ['backlog-item', sid] });
  };
  return {
    addTask: useMutation({ mutationFn: (text: string) => api.addTask(sid!, text), onSuccess: invalidate }),
    nudge: useMutation({ mutationFn: (text: string) => api.nudge(sid!, text) }),
    note: useMutation({ mutationFn: (text: string) => api.note(sid!, text) }),
    startDaemon: useMutation({ mutationFn: () => api.startDaemon(sid!, commandRevision), onSuccess: invalidate }),
    stopDaemon: useMutation({ mutationFn: (drain: boolean) => api.stopDaemon(sid!, drain, commandRevision), onSuccess: invalidate }),
    disposeBacklog: useMutation({
      mutationFn: (a: { id: string; op: 'done' | 'skip' | 'rm' }) => api.disposeBacklog(sid!, a.id, a.op),
      onSuccess: invalidate,
    }),
    stopBacklog: useMutation({
      mutationFn: (id: string) => api.stopBacklog(sid!, id),
      onSuccess: invalidate,
    }),
    setContinuous: useMutation({
      mutationFn: (a: { enabled: boolean; objective?: string }) =>
        api.setContinuous(sid!, a.enabled, a.objective ?? ''),
      onSuccess: invalidate,
    }),
  };
}

/* ------------------------------------------------------- live event stream */

const MAX_EVENTS = 2_000;

type StreamState = { events: EventMsg[]; seen: Set<string> };
type StreamAction = { kind: 'seed'; events: EventMsg[] } | { kind: 'push'; ev: EventMsg } | { kind: 'reset' };

function streamReducer(state: StreamState, action: StreamAction): StreamState {
  if (action.kind === 'reset') return { events: [], seen: new Set() };
  if (action.kind === 'seed') {
    const seen = new Set<string>();
    const events: EventMsg[] = [];
    action.events.forEach((ev, i) => {
      const k = eventKey(ev, i);
      if (!seen.has(k)) {
        seen.add(k);
        events.push(ev);
      }
    });
    return { events, seen };
  }
  // push
  const k = eventKey(action.ev, state.events.length);
  if (state.seen.has(k)) return state;
  const seen = new Set(state.seen);
  seen.add(k);
  const events = [...state.events, action.ev];
  if (events.length > MAX_EVENTS) events.splice(0, events.length - MAX_EVENTS);
  return { events, seen };
}

export interface StreamHandle {
  events: EventMsg[];
  connected: boolean;
}

/** Subscribe to a project's live event feed: REST replay seed + WS tail with
 *  auto-reconnect. Dedupes by event key so reconnect backfill never doubles. */
export function useEventStream(sid: string | null): StreamHandle {
  const [state, dispatch] = useReducer(streamReducer, { events: [], seen: new Set<string>() });
  const [connected, setConnected] = useState(false);
  const sidRef = useRef(sid);
  sidRef.current = sid;

  useEffect(() => {
    dispatch({ kind: 'reset' });
    setConnected(false);
    if (!sid) return;
    let cancelled = false;

    // seed the last window over REST so the feed is populated instantly
    api
      .events(sid, 120)
      .then((evs) => {
        if (!cancelled && sidRef.current === sid) dispatch({ kind: 'seed', events: evs });
      })
      .catch(() => {});

    const close = openStream(sid, (ev) => dispatch({ kind: 'push', ev }), {
      replay: 40,
      onOpen: () => setConnected(true),
      onClose: () => setConnected(false),
    });
    return () => {
      cancelled = true;
      close();
    };
  }, [sid]);

  return { events: state.events, connected };
}
