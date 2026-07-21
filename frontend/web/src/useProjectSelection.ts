import { type QueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { resolveProjectSelection, reconcileProjectSelection } from '../../core/src/projects';
import { api, type ProjectRow } from './api';
import { type NoticeTone } from './components/ActionNotice';

export type ProjectHistoryMode = 'push' | 'replace';

const BROWSER_PROJECT_KEY = 'argus.browser.project.v1';

function storedBrowserProject(): string | null {
  try {
    return window.sessionStorage.getItem(BROWSER_PROJECT_KEY);
  } catch {
    return null;
  }
}

function storeBrowserProject(id: string | null): void {
  try {
    if (id) window.sessionStorage.setItem(BROWSER_PROJECT_KEY, id);
    else window.sessionStorage.removeItem(BROWSER_PROJECT_KEY);
  } catch {
    /* storage can be disabled; the URL remains authoritative */
  }
}

function writeProjectLocation(id: string | null, mode: ProjectHistoryMode): void {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('project', id);
  else url.searchParams.delete('project');
  const method = mode === 'push' ? 'pushState' : 'replaceState';
  window.history[method](window.history.state, '', url.toString());
}

interface UseProjectSelectionOptions {
  cancelActiveMessage: () => void;
  notify: (tone: NoticeTone, message: string) => void;
  projects: ProjectRow[];
  projectsError: boolean;
  projectsReady: boolean;
  queryClient: QueryClient;
  setArtifactPath: (path: string | null) => void;
  setSidebarOpen: (open: boolean) => void;
  setTaskItemId: (id: string | null) => void;
}

export function useProjectSelection({
  cancelActiveMessage,
  notify,
  projects,
  projectsError,
  projectsReady,
  queryClient,
  setArtifactPath,
  setSidebarOpen,
  setTaskItemId,
}: UseProjectSelectionOptions) {
  const params = new URLSearchParams(window.location.search);
  const [sid, setSid] = useState<string | null>(
    params.get('project') || storedBrowserProject(),
  );
  const sidRef = useRef(sid);
  const initialSelectionResolvedRef = useRef(false);
  sidRef.current = sid;

  const activateProject = useCallback((id: string | null) => {
    if (id !== sidRef.current) {
      cancelActiveMessage();
      setArtifactPath(null);
      setTaskItemId(null);
    }
    sidRef.current = id;
    setSid(id);
    storeBrowserProject(id);
  }, [cancelActiveMessage, setArtifactPath, setTaskItemId]);

  const selectProject = useCallback((id: string, mode: ProjectHistoryMode = 'push') => {
    const locationId = new URLSearchParams(window.location.search).get('project');
    activateProject(id);
    if (locationId !== id) writeProjectLocation(id, mode);
  }, [activateProject]);

  const clearProjectSelection = useCallback((mode: ProjectHistoryMode = 'replace') => {
    const locationId = new URLSearchParams(window.location.search).get('project');
    activateProject(null);
    if (locationId != null) writeProjectLocation(null, mode);
  }, [activateProject]);

  const prefetchProject = useCallback((id: string) => {
    void queryClient.prefetchQuery({
      queryKey: ['snapshot', id],
      queryFn: ({ signal }) => api.snapshot(id, signal),
      staleTime: 3_000,
    });
  }, [queryClient]);

  useEffect(() => {
    if (!projectsReady) return;
    const wasResolved = initialSelectionResolvedRef.current;
    const selection = reconcileProjectSelection(
      projects,
      sidRef.current,
      wasResolved,
    );
    if (wasResolved) return;
    initialSelectionResolvedRef.current = true;
    if (selection.id !== sidRef.current) activateProject(selection.id);
    else storeBrowserProject(selection.id);
    const locationId = new URLSearchParams(window.location.search).get('project');
    if (locationId !== selection.id) {
      writeProjectLocation(selection.id, 'replace');
    }
    if (selection.recovered) {
      const fallback = projects.find((project) => project.id === selection.id);
      notify(
        'info',
        fallback
          ? `Project “${selection.requested}” was not found. Switched to ${fallback.label || fallback.id}.`
          : `Project “${selection.requested}” was not found. Create a daemon to continue.`,
      );
    }
  }, [activateProject, notify, projects, projectsReady]);

  useEffect(() => {
    const onPopState = () => {
      const requested = new URLSearchParams(window.location.search).get('project');
      setSidebarOpen(false);
      if (!requested) {
        activateProject(null);
        return;
      }
      if (!projectsReady) {
        activateProject(requested);
        return;
      }
      const selection = resolveProjectSelection(projects, requested);
      activateProject(selection.id);
      if (selection.recovered) {
        writeProjectLocation(selection.id, 'replace');
        const fallback = projects.find((project) => project.id === selection.id);
        notify(
          'info',
          fallback
            ? `Project “${selection.requested}” was not found. Switched to ${fallback.label || fallback.id}.`
            : `Project “${selection.requested}” was not found. Create a daemon to continue.`,
        );
      }
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [activateProject, notify, projects, projectsReady, setSidebarOpen]);

  const activeSid = projectsReady
    ? sid && projects.some((project) => project.id === sid)
      ? sid
      : null
    : projectsError
    ? sid
    : null;

  return {
    activateProject,
    activeSid,
    clearProjectSelection,
    prefetchProject,
    selectProject,
    sid,
    sidRef,
  };
}
