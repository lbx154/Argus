import { type QueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { resolveProjectSelection, reconcileProjectSelection } from '../../core/src/projects';
import { type ProjectRow } from './api';
import { snapshotQuery } from './hooks';
import { type NoticeTone } from './components/ActionNotice';

export type ProjectHistoryMode = 'push' | 'replace';

const BROWSER_PROJECT_KEY = 'argus.browser.project.v1';
// The project this browser last showed. A tab keeps its own choice; a fresh
// load (new tab, reopened app) opens the last one viewed here, never merely the
// most recently active one, so a message goes where the person was looking.
const LAST_VIEWED_PROJECT_KEY = 'argus.browser.lastViewedProject.v1';

function storedBrowserProject(): string | null {
  try {
    const tab = window.sessionStorage.getItem(BROWSER_PROJECT_KEY);
    if (tab) return tab;
  } catch {
    /* storage can be disabled */
  }
  try {
    return window.localStorage.getItem(LAST_VIEWED_PROJECT_KEY);
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
  try {
    // Leaving to the landing page keeps the last real project to come back to.
    if (id) window.localStorage.setItem(LAST_VIEWED_PROJECT_KEY, id);
  } catch {
    /* storage can be disabled */
  }
}

/** Forget a project this browser remembered (it was deleted or discarded). */
export function forgetBrowserProject(id: string): void {
  try {
    if (window.sessionStorage.getItem(BROWSER_PROJECT_KEY) === id) window.sessionStorage.removeItem(BROWSER_PROJECT_KEY);
  } catch {
    /* storage can be disabled */
  }
  try {
    if (window.localStorage.getItem(LAST_VIEWED_PROJECT_KEY) === id) window.localStorage.removeItem(LAST_VIEWED_PROJECT_KEY);
  } catch {
    /* storage can be disabled */
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
  /** Localized text for a URL project that no longer exists; English fallback otherwise. */
  formatProjectMissing?: (requested: string | null, fallbackLabel?: string) => string;
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
  formatProjectMissing,
  projects,
  projectsError,
  projectsReady,
  queryClient,
  setArtifactPath,
  setSidebarOpen,
  setTaskItemId,
}: UseProjectSelectionOptions) {
  const projectMissingText = (requested: string | null, fallbackLabel?: string): string => {
    if (formatProjectMissing) return formatProjectMissing(requested, fallbackLabel);
    return fallbackLabel
      ? `Project “${requested}” was not found. Switched to ${fallbackLabel}.`
      : `Project “${requested}” was not found. Create a project to continue.`;
  };
  const params = new URLSearchParams(window.location.search);
  // Only a project named in the URL is worth a "not found" notice; a
  // remembered one that has since been removed falls back quietly.
  const urlRequestedRef = useRef(Boolean(params.get('project')));
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
    void queryClient.prefetchQuery(snapshotQuery(id));
  }, [queryClient]);

  useEffect(() => {
    if (!projectsReady) return;
    const wasResolved = initialSelectionResolvedRef.current;
    const selection = reconcileProjectSelection(
      projects,
      sidRef.current,
      wasResolved,
    );
    initialSelectionResolvedRef.current = true;
    if (selection.id !== sidRef.current) activateProject(selection.id);
    else storeBrowserProject(selection.id);
    const locationId = new URLSearchParams(window.location.search).get('project');
    if (locationId !== selection.id) {
      writeProjectLocation(selection.id, 'replace');
    }
    if (selection.recovered && (wasResolved || urlRequestedRef.current)) {
      const fallback = projects.find((project) => project.id === selection.id);
      notify(
        'info',
        projectMissingText(selection.requested, fallback ? fallback.label || fallback.id : undefined),
      );
    }
  }, [activateProject, formatProjectMissing, notify, projects, projectsReady]);

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
          projectMissingText(selection.requested, fallback ? fallback.label || fallback.id : undefined),
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
