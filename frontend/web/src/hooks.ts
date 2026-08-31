import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useReducer, useRef, useState } from 'react';
import { api, isAuthenticationError, openStream, type EventMsg, type ProjectIndex, type Snapshot } from './api';
import { eventKey } from './lib/eventRender';
import { cacheProjectName } from './lib/projectName';

/* ------------------------------------------------------------------ REST */

export const PROJECT_POLL_MS = 15_000;
export const PROJECT_COST_POLL_MS = 5_000;
export const SNAPSHOT_POLL_MS = 8_000;
export const ACTIVE_DAEMON_POLL_MS = 2_000;
export const ARTIFACTS_POLL_MS = 10_000;
export const GIT_DIFF_POLL_MS = 10_000;

export function queryRetryPolicy(failureCount: number, error: unknown): boolean {
  return !isAuthenticationError(error) && failureCount < 1;
}

export function projectCostPollInterval(error: unknown): number | false {
  return isAuthenticationError(error) ? false : PROJECT_COST_POLL_MS;
}

export function projectPollInterval(index?: ProjectIndex): number {
  return index?.projects.some((project) => project.daemon_alive)
    ? ACTIVE_DAEMON_POLL_MS
    : PROJECT_POLL_MS;
}

export function snapshotPollInterval(snapshot?: Snapshot): number {
  return snapshot?.daemon.alive ? ACTIVE_DAEMON_POLL_MS : SNAPSHOT_POLL_MS;
}

export const useProjects = () =>
  useQuery({
    queryKey: ['projects'],
    queryFn: api.projectIndex,
    refetchInterval: (query) => projectPollInterval(query.state.data),
  });

export const useProjectCosts = () =>
  useQuery({
    queryKey: ['project-costs'],
    queryFn: ({ signal }) => api.projectCosts(signal),
    retry: queryRetryPolicy,
    refetchInterval: (query) => projectCostPollInterval(query.state.error),
    refetchIntervalInBackground: false,
  });

export const useSnapshot = (sid: string | null) =>
  useQuery({
    queryKey: ['snapshot', sid],
    queryFn: ({ signal }) => api.activeSnapshot(sid!, signal),
    enabled: !!sid,
    refetchInterval: (query) => snapshotPollInterval(query.state.data),
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
    refetchInterval: enabled ? ARTIFACTS_POLL_MS : false,
  });

export const useArtifact = (
  sid: string | null,
  path: string | null,
  version: string | number | null = null,
) =>
  useQuery({
    queryKey: ['artifact', sid, path, version],
    queryFn: ({ signal }) => api.artifact(sid!, path!, signal),
    enabled: !!sid && !!path,
  });

export const useGitDiff = (sid: string | null, enabled = true) =>
  useQuery({
    queryKey: ['git-diff', sid],
    queryFn: ({ signal }) => api.gitDiff(sid!, signal),
    enabled: !!sid && enabled,
    refetchInterval: enabled ? GIT_DIFF_POLL_MS : false,
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
    forceStopDaemon: useMutation({ mutationFn: () => api.stopDaemon(sid!, false, commandRevision, true), onSuccess: invalidate }),
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
      onSuccess: async () => {
        const deletedSid = sid;
        if (deletedSid) {
          const belongsToDeletedProject = (query: { queryKey: readonly unknown[] }) =>
            query.queryKey.some((part) => part === deletedSid);
          await qc.cancelQueries({ predicate: belongsToDeletedProject });
          qc.removeQueries({ predicate: belongsToDeletedProject });
        }
        await qc.invalidateQueries({ queryKey: ['projects'] });
      },
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
  | { kind: 'push-many'; sid: string; events: EventMsg[] }
  | { kind: 'reset'; sid: string | null };

export function streamReducer(state: StreamState, action: StreamAction): StreamState {
  if (action.kind === 'reset') {
    return { sid: action.sid, events: [], seen: new Set() };
  }
  if (action.sid !== state.sid) return state;
  if (action.kind === 'seed') {
    const seen = new Set<string>();
    const events: EventMsg[] = [];
    [...action.events, ...state.events].forEach((ev, i) => {
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
  // Live provider streams may deliver many fragments in one display frame.
  // Clone the retained window once per batch rather than once per event so the
  // whole React cockpit does not rerender for every protocol fragment.
  const incoming = action.kind === 'push' ? [action.ev] : action.events;
  let events: EventMsg[] | null = null;
  let seen: Set<string> | null = null;
  for (const ev of incoming) {
    const currentEvents = events ?? state.events;
    const currentSeen = seen ?? state.seen;
    const k = eventKey(ev, currentEvents.length);
    if (currentSeen.has(k)) continue;
    if (!events || !seen) {
      events = [...state.events];
      seen = new Set(state.seen);
    }
    seen.add(k);
    events.push(ev);
  }
  if (!events || !seen) return state;
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

// Twenty-five visual updates per second is ample for a readable event stream
// and leaves WebView2 time for typing, scrolling, layout and native chrome.
export const STREAM_RENDER_BATCH_MS = 40;

const ARTIFACT_REFRESH_EVENT_TYPES = new Set([
  'manager.live_view.updated',
  'round.review.completed',
  'life.mission.completed',
]);

/** Return a stable key when a streamed event can change the right-side preview.
 * Polling remains a low-frequency safety net; this key drives the immediate path.
 */
export function artifactRefreshEventKey(events: EventMsg[]): string {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    const type = String(event.type ?? '');
    if (
      ARTIFACT_REFRESH_EVENT_TYPES.has(type)
      || (type === 'engineer.progress' && event.kind === 'file_change')
    ) {
      return eventKey(event, i);
    }
  }
  return '';
}

const SNAPSHOT_REFRESH_EVENT_TYPES = new Set([
  'life.operator_question.pending',
  'life.operator_question.answered',
  'life.planner.task_added',
  'life.planner.verdict',
  'life.mission.started',
  'life.mission.completed',
  'life.mission.failed',
  'round.review.completed',
]);

/** Return a stable key when live state changed in a way the snapshot owns. */
export function snapshotRefreshEventKey(events: EventMsg[]): string {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    if (SNAPSHOT_REFRESH_EVENT_TYPES.has(String(event.type ?? ''))) {
      return eventKey(event, i);
    }
  }
  return '';
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
    let pendingEvents: EventMsg[] = [];
    let flushTimer: number | undefined;
    const flushEvents = () => {
      flushTimer = undefined;
      if (cancelled || sidRef.current !== sid || pendingEvents.length === 0) {
        pendingEvents = [];
        return;
      }
      const events = pendingEvents;
      pendingEvents = [];
      dispatch({ kind: 'push-many', sid, events });
    };

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
        pendingEvents.push(ev);
        if (flushTimer === undefined) {
          flushTimer = window.setTimeout(flushEvents, STREAM_RENDER_BATCH_MS);
        }
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
      if (flushTimer !== undefined) window.clearTimeout(flushTimer);
      pendingEvents = [];
      close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, reconnectKey]);

  return {
    events: state.sid === sid ? state.events : [],
    connected: connection.sid === sid && connection.connected,
  };
}
