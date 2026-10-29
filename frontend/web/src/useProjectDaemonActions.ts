import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { rankProjects } from '../../core/src/projects';
import { api, type ProjectRow } from './api';
import { type NoticeAction, type NoticeTone } from './components/ActionNotice';
import { type useProjectActions } from './hooks';
import { type ProjectHistoryMode } from './useProjectSelection';
import { translate } from './i18n';
import { errorText } from './lib/format';

type ContinuousSetting = { enabled: boolean; open_ended?: boolean; objective: string };

/** Keep going is the standing campaign; a finite task's campaign ends when it is done. */
export function keepsGoing(continuous: ContinuousSetting | null | undefined): boolean {
  return Boolean(continuous?.enabled && continuous.open_ended);
}

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
  continuous: ContinuousSetting | null | undefined;
  notify: (tone: NoticeTone, message: string, details?: unknown, action?: NoticeAction) => void;
  refetchProjects: () => Promise<RefetchedProjects>;
  selectProject: (id: string, mode?: ProjectHistoryMode) => void;
  setDaemonManageOpen: (open: boolean) => void;
  t?: (key: string, variables?: Record<string, string | number>) => string;
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
  t = (key, variables) => translate(key, variables),
}: UseProjectDaemonActionsOptions) {
  const daemonBusy = actions.startDaemon.isPending
    || actions.stopDaemon.isPending
    || manageActions.startDaemon.isPending
    || manageActions.updateProject.isPending
    || manageActions.deleteProject.isPending;

  const actionFeedback = useCallback((success: string) => ({
    onSuccess: () => notify('success', success),
    onError: (error: Error) => notify('error', errorText(error), error),
  }), [notify]);

  const requestStartDaemon = useCallback(() =>
    actions.startDaemon.mutate(undefined, actionFeedback(t('daemon.startRequested'))),
  [actionFeedback, actions.startDaemon, t]);

  /** Immediate interruption; callers confirm with the operator first. */
  const requestStopDaemon = useCallback(() =>
    actions.stopDaemon.mutate(undefined, actionFeedback(t('daemon.stopRequested'))),
  [actionFeedback, actions.stopDaemon, t]);

  const manageStartDaemon = useCallback(async (): Promise<boolean> => {
    try {
      await manageActions.startDaemon.mutateAsync();
      notify('success', t('daemon.resumed'));
      return true;
    } catch (error) {
      notify('error', errorText(error), error);
      return false;
    }
  }, [manageActions.startDaemon, notify, t]);

  const manageRenameProject = useCallback(async (name: string): Promise<boolean> => {
    if (!manageTargetSid) return false;
    try {
      await manageActions.updateProject.mutateAsync({ sid: manageTargetSid, name });
      notify('success', t('project.renamed'));
      return true;
    } catch (error) {
      notify('error', errorText(error), error);
      return false;
    }
  }, [manageActions.updateProject, manageTargetSid, notify, t]);

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
      notify('success', t('project.trashed'), undefined, {
        label: t('project.undoDelete'),
        onClick: async () => {
          try {
            const restored = await api.restoreTrash(deleted.trash_path.split(/[\\/]/).at(-1)!);
            await refetchProjects();
            selectProject(restored.sid);
            notify('success', t('project.restored'));
          } catch (error) {
            notify('error', errorText(error), error);
          }
        },
      });
      return true;
    } catch (error) {
      notify('error', errorText(error), error);
      return false;
    }
  }, [activeSid, clearProjectSelection, manageActions.deleteProject, manageTargetSid, notify, refetchProjects, selectProject, setDaemonManageOpen, setManageTargetSid, t]);

  const requestManageSession = useCallback((projectId: string) => {
    setManageTargetSid(projectId);
    setDaemonManageOpen(true);
  }, [setDaemonManageOpen, setManageTargetSid]);

  const toggleContinuous = useCallback(() => {
    if (!continuous) return;
    const enabled = !keepsGoing(continuous);
    actions.setContinuous.mutate(
      { enabled, objective: continuous.objective },
      actionFeedback(enabled ? t('campaign.enabled') : t('campaign.stopped')),
    );
  }, [actionFeedback, actions.setContinuous, continuous, t]);

  return {
    daemonBusy,
    manageDeleteProject,
    manageRenameProject,
    manageStartDaemon,
    requestManageSession,
    requestStartDaemon,
    requestStopDaemon,
    toggleContinuous,
  };
}
