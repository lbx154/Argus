import { type QueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { createSessionFast } from './lib/createDaemonFlow';
import { api, type ProjectRow } from './api';
import { type NoticeTone } from './components/ActionNotice';
import { errorText } from './lib/format';

interface UseCreateDaemonSessionOptions {
  localCwd: string;
  notify: (tone: NoticeTone, message: string, details?: unknown) => void;
  onFocusComposer: () => void;
  queryClient: QueryClient;
  refetchProjects: () => Promise<unknown>;
  selectProject: (id: string) => void;
  translate?: (key: string, variables?: Record<string, string | number>) => string;
  onCreated?: (sid: string, draft: string) => void;
}

export function useCreateDaemonSession({
  localCwd,
  notify,
  onFocusComposer,
  queryClient,
  refetchProjects,
  selectProject,
  translate,
  onCreated,
}: UseCreateDaemonSessionOptions) {
  const [creatingDaemon, setCreatingDaemon] = useState(false);
  const creatingDaemonRef = useRef(false);

  const createDaemon = async (
    name: string,
    objective: string,
    workdir: string,
    draft = '',
  ): Promise<boolean> => {
    if (creatingDaemonRef.current) return false;
    creatingDaemonRef.current = true;
    setCreatingDaemon(true);
    try {
      const { created: result, startCampaign } = await createSessionFast(
        api,
        name,
        objective,
        workdir,
      );
      const outputWorkdir = String(result.workdir || workdir || '');
      queryClient.setQueryData<{
        projects: ProjectRow[];
        local_cwd: string;
      }>(['projects'], (current) => ({
        local_cwd: current?.local_cwd ?? localCwd,
        projects: [
          {
            id: result.sid,
            label: name || result.sid,
            display_name: name,
            objective: '',
            launch_cwd: outputWorkdir,
            workdir: outputWorkdir,
            last_active: Date.now() / 1_000,
            daemon_alive: false,
            daemon_pid: null,
            uptime_seconds: null,
          },
          ...(current?.projects ?? []).filter((project) => project.id !== result.sid),
        ],
      }));
      onCreated?.(result.sid, draft);
      selectProject(result.sid);
      void refetchProjects();
      window.setTimeout(onFocusComposer, 0);
      notify(
        startCampaign ? 'info' : 'success',
        startCampaign
          ? (translate ? translate('new.createdStarting') : 'Session created and selected. Campaign is starting in the background.')
          : (translate ? translate('new.createdIdle') : 'Session created and selected.'),
      );
      if (startCampaign) {
        void startCampaign()
          .then(() => {
            void queryClient.invalidateQueries({ queryKey: ['snapshot', result.sid] });
            void refetchProjects();
            notify('success', translate ? translate('new.campaignStarted') : 'Work started.');
          })
          .catch((error) => {
            notify(
              'error',
              translate
                ? translate('new.campaignFailed', { error: errorText(error) })
                : `The project was created, but its work could not start: ${errorText(error)}`,
              error,
            );
          });
      }
      return true;
    } catch (error) {
      notify('error', translate ? translate('new.createFailed', { error: errorText(error) }) : `Could not create the project: ${errorText(error)}`, error);
      return false;
    } finally {
      creatingDaemonRef.current = false;
      setCreatingDaemon(false);
    }
  };

  return {
    createDaemon,
    creatingDaemon,
  };
}
