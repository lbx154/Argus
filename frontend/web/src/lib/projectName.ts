import type { QueryClient } from '@tanstack/react-query';
import type { ProjectRow, Snapshot } from '../../../core/src/types';

interface ProjectIndexCache {
  projects: ProjectRow[];
  local_cwd?: string;
}

export function cacheProjectName(
  queryClient: QueryClient,
  sid: string,
  name: string,
): void {
  queryClient.setQueryData<Snapshot>(['snapshot', sid], (snapshot) => (
    snapshot
      ? {
          ...snapshot,
          session: { ...snapshot.session, display_name: name },
        }
      : snapshot
  ));
  queryClient.setQueryData<ProjectIndexCache>(['projects'], (index) => (
    index
      ? {
          ...index,
          projects: index.projects.map((project) => (
            project.id === sid
              ? {
                  ...project,
                  display_name: name,
                  label: name || project.objective || project.id,
                }
              : project
          )),
        }
      : index
  ));
}
