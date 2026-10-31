import { type QueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { api, type ProjectRow } from './api';

interface UseCreateDaemonSessionOptions {
  localCwd: string;
  onFocusComposer: () => void;
  queryClient: QueryClient;
  refetchProjects: () => Promise<unknown>;
  selectProject: (id: string) => void;
}

export function useCreateDaemonSession({
  localCwd,
  onFocusComposer,
  queryClient,
  refetchProjects,
  selectProject,
}: UseCreateDaemonSessionOptions) {
  const [createError, setCreateError] = useState<unknown>(null);
  const clearCreateError = useCallback(() => setCreateError(null), []);

  /** The new project, listed and selected; null when it could not be made, the error kept. */
  const createDaemon = async (name: string, workdir: string): Promise<string | null> => {
    setCreateError(null);
    try {
      const result = await api.createDaemon('', name, workdir);
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
            workdir: outputWorkdir,
            last_active: Date.now() / 1_000,
            daemon_alive: false,
            daemon_pid: null,
          },
          ...(current?.projects ?? []).filter((project) => project.id !== result.sid),
        ],
      }));
      selectProject(result.sid);
      void refetchProjects();
      window.setTimeout(onFocusComposer, 0);
      return result.sid;
    } catch (error) {
      setCreateError(error);
      return null;
    }
  };

  return {
    createDaemon,
    createError,
    clearCreateError,
  };
}
