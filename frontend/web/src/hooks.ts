import { queryOptions, replaceEqualDeep, useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api, isAuthenticationError, openProjectStates, type ProjectIndex, type Snapshot } from './api';
import { POLL_CAPABILITY, useCapability } from './lib/capabilities';
import { cacheProjectName } from './lib/projectName';
import { workspaceApi } from './research-workbench/workspaceApi';

/** Trailing-edge throttle for hot props. Streamed events arrive many times a
 * second while agents work; the memoized map only needs a beat-level view. */
export function useThrottledValue<T>(value: T, ms: number): T {
  const [current, setCurrent] = useState(value);
  const latest = useRef(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stamp = useRef(0);
  latest.current = value;
  useEffect(() => {
    if (value === current) return;
    const due = stamp.current + ms - Date.now();
    if (due <= 0) {
      stamp.current = Date.now();
      setCurrent(value);
      return;
    }
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      stamp.current = Date.now();
      setCurrent(latest.current);
    }, due);
  }, [value, current, ms]);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  return current;
}

/* ------------------------------------------------------------------ REST */

export const PROJECT_POLL_MS = 15_000;
export const SNAPSHOT_POLL_MS = 8_000;
export const ACTIVE_DAEMON_POLL_MS = 2_000;

export function queryRetryPolicy(failureCount: number, error: unknown): boolean {
  return !isAuthenticationError(error) && failureCount < 1;
}

export function projectPollInterval(index?: ProjectIndex): number {
  return index?.projects.some((project) => project.daemon_alive)
    ? ACTIVE_DAEMON_POLL_MS
    : PROJECT_POLL_MS;
}

export function snapshotPollInterval(snapshot?: Snapshot): number {
  return snapshot?.daemon.alive || snapshot?.manager_requests?.some(request => request.status === 'running')
    ? ACTIVE_DAEMON_POLL_MS
    : SNAPSHOT_POLL_MS;
}

export function useProjects() {
  const polled = useCapability(POLL_CAPABILITY);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ['projects'],
    queryFn: api.projectIndex,
    refetchInterval: (query) => polled && projectPollInterval(query.state.data),
  });
  const listed = query.data !== undefined;
  // A server that pushes state sends the list as the page connects, then each project's row and
  // snapshot as it changes. It is followed once the first list is in, which it then supersedes.
  useEffect(() => {
    if (polled || !listed) return;
    return openProjectStates((state) => {
      if (state.type === 'projects') {
        queryClient.setQueryData<ProjectIndex>(['projects'], { projects: state.projects, local_cwd: state.local_cwd });
        // A snapshot the page holds may be older than the connection: it is read again.
        void queryClient.invalidateQueries({ queryKey: ['snapshot'] });
      } else {
        const { id, row, snapshot } = state;
        queryClient.setQueryData<ProjectIndex>(['projects'], (index) => index && {
          ...index, projects: [...index.projects.filter((project) => project.id !== id), ...row ? [row] : []],
        });
        if (snapshot) queryClient.setQueryData(['snapshot', id], snapshot);
        if (!row) return;
      }
      // The roles learn in their turns, and a turn's end is a change.
      void queryClient.invalidateQueries({ queryKey: ['learning-status'] });
    });
  }, [polled, listed, queryClient]);
  return query;
}

/** A project's snapshot, however it arrives: read, read again, or pushed. A reply read before a
 * pushed snapshot can arrive after it, so of two the page keeps the one the server read later. */
export const snapshotQuery = (sid: string | null) => queryOptions({
  queryKey: ['snapshot', sid],
  queryFn: ({ signal }) => api.snapshot(sid!, signal),
  structuralSharing: (held: unknown, next: unknown) =>
    ((held as Snapshot | undefined)?.read_at ?? 0) > ((next as Snapshot).read_at ?? 0) ? held : replaceEqualDeep(held, next),
});

export function useSnapshot(sid: string | null) {
  const polled = useCapability(POLL_CAPABILITY);
  return useQuery({
    ...snapshotQuery(sid),
    enabled: !!sid,
    refetchInterval: (query) => polled && snapshotPollInterval(query.state.data),
  });
}

export const useConfig = (sid: string | null, enabled: boolean) =>
  useQuery({ queryKey: ['config', sid], queryFn: ({ signal }) => api.config(sid!, signal), enabled: !!sid && enabled });

export const useModelChoices = (sid: string | null, enabled: boolean) =>
  useQuery({ queryKey: ['model-choices', sid], queryFn: ({ signal }) => api.modelChoices(sid!, signal), enabled: !!sid && enabled });

/** The project's workspace listing, fetched while the composer asks for a path (again once stale); shares the IDE's cache. */
export const useWorkspaceTree = (sid: string | null, enabled: boolean) =>
  useQuery({
    queryKey: ['workspace-tree', sid, `project:${sid}`],
    queryFn: ({ signal }) => workspaceApi.tree(sid!, `project:${sid}`, signal),
    enabled: !!sid && enabled,
    staleTime: 30_000,
    retry: false,
  });

export const useArtifacts = (sid: string | null, enabled = true) =>
  useQuery({
    queryKey: ['artifacts', sid],
    queryFn: ({ signal }) => api.artifacts(sid!, signal),
    enabled: !!sid && enabled,
  });

export const useArtifact = (
  sid: string | null,
  path: string | null,
  version: string | number | null = null,
  delivery?: string,
) =>
  useQuery({
    queryKey: ['artifact', sid, path, version, delivery],
    queryFn: ({ signal }) => api.artifact(sid!, path!, signal, delivery),
    enabled: !!sid && !!path,
    retry: delivery ? false : undefined,
    // The Reviewer edits this same file every round. Keep an open opinion
    // current without remounting the page or reloading PDF previews.
    refetchInterval: (query) => !delivery && path && /(?:^|[\\/])REVIEW\.md$/i.test(path)
      && !isAuthenticationError(query.state.error) ? 2_000 : false,
  });

export const useBacklogItem = (sid: string | null, itemId: string | null, letter?: string) =>
  useQuery({
    queryKey: letter ? ['backlog-item', sid, itemId, letter] : ['backlog-item', sid, itemId],
    queryFn: ({ signal }) => letter ? api.backlogItem(sid!, itemId!, signal, letter) : api.backlogItem(sid!, itemId!, signal),
    enabled: !!sid && !!itemId,
  });

/* --------------------------------------------------------------- mutations */

export function useProjectActions(sid: string | null) {
  const qc = useQueryClient();
  const invalidateProject = (targetSid: string | null) => {
    qc.invalidateQueries({ queryKey: ['snapshot', targetSid] });
    qc.invalidateQueries({ queryKey: ['projects'] });
    qc.invalidateQueries({ queryKey: ['backlog-item', targetSid] });
  };
  const invalidate = () => invalidateProject(sid);
  return {
    startDaemon: useMutation({ mutationFn: () => api.startDaemon(sid!), onSuccess: invalidate }),
    stopDaemon: useMutation({ mutationFn: () => api.stopDaemon(sid!), onSuccess: invalidate }),
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
        qc.setQueryData<ProjectIndex>(['projects'], current => current && ({
          ...current, projects: current.projects.filter(project => project.id !== deletedSid),
        }));
        if (deletedSid) {
          const belongsToDeletedProject = (query: { queryKey: readonly unknown[] }) =>
            query.queryKey.some((part) => part === deletedSid);
          await qc.cancelQueries({ predicate: belongsToDeletedProject });
        }
        await qc.invalidateQueries({ queryKey: ['projects'] });
      },
    }),
  };
}
