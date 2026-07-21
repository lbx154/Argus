import { useCallback, useEffect, useState } from 'react';
import { rankProjects } from '../../core/src/projects';
import { type ProjectRow } from './api';
import { type NoticeTone } from './components/ActionNotice';
import { type useProjectActions } from './hooks';
import { type ProjectHistoryMode } from './useProjectSelection';

const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error || 'Unknown error');

interface RefetchedProjects {
  data?: {
    projects?: ProjectRow[];
  };
}

interface UseProjectDaemonActionsOptions {
  actions: ReturnType<typeof useProjectActions>;
  activeSid: string | null;
  clearProjectSelection: (mode?: ProjectHistoryMode) => void;
  continuous: { enabled: boolean; objective: string } | null | undefined;
  currentSnapshotSid: string | undefined;
  notify: (tone: NoticeTone, message: string) => void;
  refetchProjects: () => Promise<RefetchedProjects>;
  selectProject: (id: string, mode?: ProjectHistoryMode) => void;
  setDaemonManageOpen: (open: boolean) => void;
}

export function useProjectDaemonActions({
  actions,
  activeSid,
  clearProjectSelection,
  continuous,
  currentSnapshotSid,
  notify,
  refetchProjects,
  selectProject,
  setDaemonManageOpen,
}: UseProjectDaemonActionsOptions) {
  const [manageTargetSid, setManageTargetSid] = useState<string | null>(null);

  const daemonBusy = actions.startDaemon.isPending
    || actions.stopDaemon.isPending
    || actions.updateProject.isPending
    || actions.deleteProject.isPending;

  const actionFeedback = useCallback((success: string) => ({
    onSuccess: () => notify('success', success),
    onError: (error: Error) => notify('error', errorText(error)),
  }), [notify]);

  const requestStartDaemon = useCallback(() =>
    actions.startDaemon.mutate(undefined, actionFeedback('Daemon start requested.')),
  [actionFeedback, actions.startDaemon]);

  const requestStopDaemon = useCallback(() =>
    actions.stopDaemon.mutate(true, actionFeedback('Daemon is draining and will stop safely.')),
  [actionFeedback, actions.stopDaemon]);

  const manageStartDaemon = useCallback(async (): Promise<boolean> => {
    try {
      await actions.startDaemon.mutateAsync();
      notify('success', 'Daemon resumed.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  }, [actions.startDaemon, notify]);

  const managePauseDaemon = useCallback(async (): Promise<boolean> => {
    try {
      await actions.stopDaemon.mutateAsync(true);
      notify('success', 'Daemon paused safely.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  }, [actions.stopDaemon, notify]);

  const manageRenameProject = useCallback(async (name: string): Promise<boolean> => {
    if (!activeSid) return false;
    try {
      await actions.updateProject.mutateAsync({ sid: activeSid, name });
      notify('success', 'Session name updated.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  }, [actions.updateProject, activeSid, notify]);

  const manageDeleteProject = useCallback(async (): Promise<boolean> => {
    if (!activeSid) return false;
    try {
      await actions.deleteProject.mutateAsync();
      setDaemonManageOpen(false);
      clearProjectSelection('replace');
      const refreshed = await refetchProjects();
      const next = rankProjects(refreshed.data?.projects ?? [])[0];
      if (next) selectProject(next.id, 'replace');
      notify('success', 'Session moved to recoverable trash.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  }, [actions.deleteProject, activeSid, clearProjectSelection, notify, refetchProjects, selectProject, setDaemonManageOpen]);

  const requestManageSession = useCallback((projectId: string) => {
    setDaemonManageOpen(false);
    if (projectId === activeSid && currentSnapshotSid === projectId) {
      setManageTargetSid(null);
      setDaemonManageOpen(true);
      return;
    }
    setManageTargetSid(projectId);
    selectProject(projectId);
  }, [activeSid, currentSnapshotSid, selectProject, setDaemonManageOpen]);

  useEffect(() => {
    if (!manageTargetSid || activeSid !== manageTargetSid || currentSnapshotSid !== manageTargetSid) return;
    setManageTargetSid(null);
    setDaemonManageOpen(true);
  }, [activeSid, currentSnapshotSid, manageTargetSid, setDaemonManageOpen]);

  const requestDispose = useCallback((id: string, op: 'done' | 'skip' | 'rm') =>
    actions.disposeBacklog.mutate(
      { id, op },
      {
        onSuccess: () => notify('success', op === 'done' ? 'Work marked done.' : 'Work removed.'),
        onError: (error: Error) => notify('error', errorText(error)),
      },
    ),
  [actions.disposeBacklog, notify]);

  const requestStopIteration = useCallback((id: string) =>
    actions.stopBacklog.mutate(id, {
      onSuccess: () => notify('success', 'Iteration stopped.'),
      onError: (error: Error) => notify('error', errorText(error)),
    }),
  [actions.stopBacklog, notify]);

  const toggleContinuous = useCallback(() => {
    if (!continuous) return;
    const enabled = !continuous.enabled;
    actions.setContinuous.mutate(
      { enabled, objective: continuous.objective },
      actionFeedback(enabled ? 'Continuous campaign enabled.' : 'Continuous campaign stopped.'),
    );
  }, [actionFeedback, actions.setContinuous, continuous]);

  return {
    daemonBusy,
    manageDeleteProject,
    managePauseDaemon,
    manageRenameProject,
    manageStartDaemon,
    requestDispose,
    requestManageSession,
    requestStartDaemon,
    requestStopDaemon,
    requestStopIteration,
    toggleContinuous,
  };
}
