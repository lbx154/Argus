import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useReducer, useRef, useState } from 'react';
import { api, openStream, type EventMsg } from './api';
import { eventKey } from './lib/eventRender';
import { cacheProjectName } from './lib/projectName';

/* ------------------------------------------------------------------ REST */

export const useProjects = () =>
  useQuery({ queryKey: ['projects'], queryFn: api.projectIndex, refetchInterval: 5_000 });

export const useSnapshot = (sid: string | null) =>
  useQuery({
    queryKey: ['snapshot', sid],
    queryFn: ({ signal }) => api.snapshot(sid!, signal),
    enabled: !!sid,
    refetchInterval: 4_000,
  });

export const useStatus = (sid: string | null, enabled = true) =>
  useQuery({
    queryKey: ['status', sid],
    queryFn: ({ signal }) => api.status(sid!, signal),
    enabled: !!sid && enabled,
    refetchInterval: enabled ? 6_000 : false,
  });

export const useJournal = (sid: string | null, n = 30, enabled = true) =>
  useQuery({
    queryKey: ['journal', sid, n],
    queryFn: ({ signal }) => api.journal(sid!, n, signal),
    enabled: !!sid && enabled,
    refetchInterval: enabled ? 8_000 : false,
  });

export const useDoctor = (sid: string | null, enabled: boolean) =>
  useQuery({ queryKey: ['doctor', sid], queryFn: ({ signal }) => api.doctor(sid!, signal), enabled: !!sid && enabled });

export const useConfig = (sid: string | null, enabled: boolean) =>
  useQuery({ queryKey: ['config', sid], queryFn: ({ signal }) => api.config(sid!, signal), enabled: !!sid && enabled });

export const useIdentity = (sid: string | null, enabled: boolean) =>
  useQuery({ queryKey: ['identity', sid], queryFn: ({ signal }) => api.identity(sid!, signal), enabled: !!sid && enabled });

export const useTranscript = (sid: string | null, enabled: boolean, n = 30) =>
  useQuery({ queryKey: ['transcript', sid, n], queryFn: ({ signal }) => api.transcript(sid!, n, signal), enabled: !!sid && enabled });

export const useArtifacts = (sid: string | null, enabled = true) =>
  useQuery({
    queryKey: ['artifacts', sid],
    queryFn: ({ signal }) => api.artifacts(sid!, signal),
    enabled: !!sid && enabled,
    refetchInterval: enabled ? 3_000 : false,
  });

export const useArtifact = (sid: string | null, path: string | null) =>
  useQuery({
    queryKey: ['artifact', sid, path],
    queryFn: ({ signal }) => api.artifact(sid!, path!, signal),
    enabled: !!sid && !!path,
    refetchInterval: sid && path ? 3_000 : false,
  });

export const useGitDiff = (sid: string | null, enabled = true) =>
  useQuery({
    queryKey: ['git-diff', sid],
    queryFn: ({ signal }) => api.gitDiff(sid!, signal),
    enabled: !!sid && enabled,
    refetchInterval: enabled ? 5_000 : false,
  });

export const useBacklogItem = (sid: string | null, itemId: string | null) =>
  useQuery({
    queryKey: ['backlog-item', sid, itemId],
    queryFn: ({ signal }) => api.backlogItem(sid!, itemId!, signal),
    enabled: !!sid && !!itemId,
  });

/* --------------------------------------------------------------- mutations */

export function useProjectActions(sid: string | null, commandRevision?: number) {
  const qc = useQueryClient();
  const invalidateProject = (targetSid: string | null) => {
    qc.invalidateQueries({ queryKey: ['snapshot', targetSid] });
    qc.invalidateQueries({ queryKey: ['status', targetSid] });
    qc.invalidateQueries({ queryKey: ['projects'] });
    qc.invalidateQueries({ queryKey: ['backlog-item', targetSid] });
  };
  const invalidate = () => invalidateProject(sid);
  return {
    addTask: useMutation({ mutationFn: (text: string) => api.addTask(sid!, text), onSuccess: invalidate }),
    nudge: useMutation({ mutationFn: (text: string) => api.nudge(sid!, text) }),
    note: useMutation({ mutationFn: (text: string) => api.note(sid!, text) }),
    startDaemon: useMutation({ mutationFn: () => api.startDaemon(sid!, commandRevision), onSuccess: invalidate }),
    stopDaemon: useMutation({ mutationFn: (drain: boolean) => api.stopDaemon(sid!, drain, commandRevision), onSuccess: invalidate }),
    updateProject: useMutation({
      mutationFn: (input: { sid: string; name: string }) =>
        api.updateProject(input.sid, input.name),
      onSuccess: (result) => {
        cacheProjectName(qc, result.sid, result.name);
        invalidateProject(result.sid);
      },
    }),
    deleteProject: useMutation({
      mutationFn: () => api.deleteProject(sid!),
      onSuccess: invalidate,
    }),
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

type StreamState = { sid: string | null; events: EventMsg[]; seen: Set<string> };
type StreamAction =
  | { kind: 'seed'; sid: string; events: EventMsg[] }
  | { kind: 'push'; sid: string; ev: EventMsg }
  | { kind: 'reset'; sid: string | null };

export function streamReducer(state: StreamState, action: StreamAction): StreamState {
  if (action.kind === 'reset') {
    return { sid: action.sid, events: [], seen: new Set() };
  }
  if (action.sid !== state.sid) return state;
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
    const retained = events.slice(-MAX_EVENTS);
    return {
      sid: state.sid,
      events: retained,
      seen: new Set(retained.map((ev, i) => eventKey(ev, i))),
    };
  }
  // push
  const k = eventKey(action.ev, state.events.length);
  if (state.seen.has(k)) return state;
  const seen = new Set(state.seen);
  seen.add(k);
  const events = [...state.events, action.ev];
  if (events.length > MAX_EVENTS) {
    const removed = events.splice(0, events.length - MAX_EVENTS);
    removed.forEach((ev, i) => seen.delete(eventKey(ev, i)));
  }
  return { sid: state.sid, events, seen };
}

export interface StreamHandle {
  events: EventMsg[];
  connected: boolean;
}

/** Subscribe to a project's live event feed: REST replay seed + WS tail with
 *  auto-reconnect. Dedupes by event key so reconnect backfill never doubles. */
export function useEventStream(sid: string | null, reconnectKey = 0): StreamHandle {
  const [state, dispatch] = useReducer(streamReducer, {
    sid: null,
    events: [],
    seen: new Set<string>(),
  });
  const [connection, setConnection] = useState({
    sid: null as string | null,
    connected: false,
  });
  const sidRef = useRef(sid);
  sidRef.current = sid;

  useEffect(() => {
    dispatch({ kind: 'reset', sid });
    setConnection({ sid, connected: false });
    if (!sid) return;
    let cancelled = false;
    const controller = new AbortController();

    // seed the last window over REST so the feed is populated instantly
    api
      .events(sid, 120, controller.signal)
      .then((evs) => {
        if (!cancelled && sidRef.current === sid) {
          dispatch({ kind: 'seed', sid, events: evs });
        }
      })
      .catch(() => {});

    const close = openStream(sid, (ev) => {
      if (!cancelled && sidRef.current === sid) {
        dispatch({ kind: 'push', sid, ev });
      }
    }, {
      replay: 40,
      onOpen: () => {
        if (!cancelled && sidRef.current === sid) {
          setConnection({ sid, connected: true });
        }
      },
      onClose: () => {
        if (!cancelled && sidRef.current === sid) {
          setConnection({ sid, connected: false });
        }
      },
    });
    return () => {
      cancelled = true;
      controller.abort();
      close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, reconnectKey]);

  return {
    events: state.sid === sid ? state.events : [],
    connected: connection.sid === sid && connection.connected,
  };
}
