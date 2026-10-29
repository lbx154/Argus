import { type QueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';
import { api, type ProjectRow } from './api';

interface UseCreateDaemonSessionOptions {
  localCwd: string;
  onFocusComposer: () => void;
  queryClient: QueryClient;
  refetchProjects: () => Promise<unknown>;
  selectProject: (id: string) => void;
  onCreated?: (sid: string, draft: string) => void;
}

export function useCreateDaemonSession({
  localCwd,
  onFocusComposer,
  queryClient,
  refetchProjects,
  selectProject,
  onCreated,
}: UseCreateDaemonSessionOptions) {
  const [creatingDaemon, setCreatingDaemon] = useState(false);
  const [createError, setCreateError] = useState<unknown>(null);
  const clearCreateError = useCallback(() => setCreateError(null), []);
  const creatingDaemonRef = useRef(false);

  const createDaemon = async (
    name: string,
    workdir: string,
    draft = '',
  ): Promise<boolean> => {
    if (creatingDaemonRef.current) return false;
    creatingDaemonRef.current = true;
    setCreatingDaemon(true);
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
      return true;
    } catch (error) {
      setCreateError(error);
      return false;
    } finally {
      creatingDaemonRef.current = false;
      setCreatingDaemon(false);
    }
  };

  return {
    createDaemon,
    creatingDaemon,
    createError,
    clearCreateError,
  };
}
