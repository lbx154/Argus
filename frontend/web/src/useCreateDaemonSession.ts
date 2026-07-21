import { type QueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { createSessionFast } from './lib/createDaemonFlow';
import { api, type ProjectRow } from './api';
import { type NoticeTone } from './components/ActionNotice';

const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error || 'Unknown error');

interface UseCreateDaemonSessionOptions {
  localCwd: string;
  notify: (tone: NoticeTone, message: string) => void;
  onFocusComposer: () => void;
  queryClient: QueryClient;
  refetchProjects: () => Promise<unknown>;
  selectProject: (id: string) => void;
}

export function useCreateDaemonSession({
  localCwd,
  notify,
  onFocusComposer,
  queryClient,
  refetchProjects,
  selectProject,
}: UseCreateDaemonSessionOptions) {
  const [creatingDaemon, setCreatingDaemon] = useState(false);
  const creatingDaemonRef = useRef(false);

  const createDaemon = async (
    name: string,
    objective: string,
    workdir: string,
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
      selectProject(result.sid);
      void refetchProjects();
      window.setTimeout(onFocusComposer, 0);
      notify(
        startCampaign ? 'info' : 'success',
        startCampaign
          ? 'Session created and selected. Campaign is starting in the background.'
          : 'Session created and selected.',
      );
      if (startCampaign) {
        void startCampaign()
          .then(() => {
            void queryClient.invalidateQueries({ queryKey: ['snapshot', result.sid] });
            void refetchProjects();
            notify('success', 'Campaign started.');
          })
          .catch((error) => {
            notify(
              'error',
              `Session was created, but the campaign could not start: ${errorText(error)}`,
            );
          });
      }
      return true;
    } catch (error) {
      notify('error', `Could not create daemon: ${errorText(error)}`);
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
