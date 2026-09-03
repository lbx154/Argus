import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { rankProjects } from '../../core/src/projects';
import { type ProjectRow } from './api';
import { type NoticeTone } from './components/ActionNotice';
import { type useProjectActions } from './hooks';
import { type ProjectHistoryMode } from './useProjectSelection';

// Manual Stop follows Codex-style immediate interruption. Graceful draining
// remains available to upgrade/lifecycle flows that explicitly request it.
export const MANUAL_STOP_FORCE = true;

const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error || 'Unknown error');

interface RefetchedProjects {
  data?: {
    projects?: ProjectRow[];
  };
}

interface UseProjectDaemonActionsOptions {
  actions: ReturnType<typeof useProjectActions>;
  manageActions: ReturnType<typeof useProjectActions>;
  manageTargetSid: string | null;
  setManageTargetSid: Dispatch<SetStateAction<string | null>>;
  activeSid: string | null;
  clearProjectSelection: (mode?: ProjectHistoryMode) => void;
  continuous: { enabled: boolean; objective: string } | null | undefined;
  notify: (tone: NoticeTone, message: string) => void;
  refetchProjects: () => Promise<RefetchedProjects>;
  selectProject: (id: string, mode?: ProjectHistoryMode) => void;
  setDaemonManageOpen: (open: boolean) => void;
}

export function useProjectDaemonActions({
  actions,
  manageActions,
  manageTargetSid,
  setManageTargetSid,
  activeSid,
  clearProjectSelection,
  continuous,
  notify,
  refetchProjects,
  selectProject,
  setDaemonManageOpen,
}: UseProjectDaemonActionsOptions) {
  const daemonBusy = actions.startDaemon.isPending
    || actions.stopDaemon.isPending
    || actions.forceStopDaemon.isPending
    || manageActions.startDaemon.isPending
    || manageActions.forceStopDaemon.isPending
    || manageActions.updateProject.isPending
    || manageActions.deleteProject.isPending;

  const actionFeedback = useCallback((success: string) => ({
    onSuccess: () => notify('success', success),
    onError: (error: Error) => notify('error', errorText(error)),
  }), [notify]);

  const requestStartDaemon = useCallback(() =>
    actions.startDaemon.mutate(undefined, actionFeedback('Daemon start requested.')),
  [actionFeedback, actions.startDaemon]);

  const requestStopDaemon = useCallback(() =>
    actions.forceStopDaemon.mutate(
      undefined,
      actionFeedback('Stop requested; the verified daemon process is being interrupted.'),
    ),
  [actionFeedback, actions.forceStopDaemon]);

  const manageStartDaemon = useCallback(async (): Promise<boolean> => {
    try {
      await manageActions.startDaemon.mutateAsync();
      notify('success', 'Daemon resumed.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  }, [manageActions.startDaemon, notify]);

  const manageStopDaemon = useCallback(async (): Promise<boolean> => {
    try {
      await manageActions.forceStopDaemon.mutateAsync();
      await refetchProjects();
      notify('success', 'Daemon stopped. This session can now be deleted.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  }, [manageActions.forceStopDaemon, notify, refetchProjects]);

  const manageRenameProject = useCallback(async (name: string): Promise<boolean> => {
    if (!manageTargetSid) return false;
    try {
      await manageActions.updateProject.mutateAsync({ sid: manageTargetSid, name });
      notify('success', 'Session name updated.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  }, [manageActions.updateProject, manageTargetSid, notify]);

  const manageDeleteProject = useCallback(async (): Promise<boolean> => {
    if (!manageTargetSid) return false;
    try {
      const deletedSid = manageTargetSid;
      const deleted = await manageActions.deleteProject.mutateAsync();
      setDaemonManageOpen(false);
      setManageTargetSid(null);
      const refreshed = await refetchProjects();
      if (deletedSid === activeSid) {
        clearProjectSelection('replace');
        const next = rankProjects(refreshed.data?.projects ?? [])[0];
        if (next) selectProject(next.id, 'replace');
      }
      notify(
        'success',
        deleted.workdir_preserved
          ? `Session moved to recoverable trash. Files remain in ${deleted.workdir}.`
          : 'Session moved to recoverable trash.',
      );
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  }, [activeSid, clearProjectSelection, manageActions.deleteProject, manageTargetSid, notify, refetchProjects, selectProject, setDaemonManageOpen, setManageTargetSid]);

  const requestManageSession = useCallback((projectId: string) => {
    setManageTargetSid(projectId);
    setDaemonManageOpen(true);
  }, [setDaemonManageOpen, setManageTargetSid]);

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
    manageStopDaemon,
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
