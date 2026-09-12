import { projectKey, type TaskDeepLink } from './model';
import type { CollaborationProject, ProjectIdentity, WorkspaceIdentity } from './types';

/** A project change clears the old task; only the initial explicit link restores one. */
export function automaticProjectSelection(
  projects: readonly CollaborationProject[], selectedKey: string, entry: TaskDeepLink, respectEntry: boolean,
): { key: string; taskId: string } | null {
  if (projects.some(project => projectKey(project) === selectedKey)) return null;
  const matches = respectEntry && entry.sid
    ? projects.filter(project => project.sid === entry.sid && (!entry.tenant || project.tenant_id === entry.tenant)) : [];
  const linked = matches.length === 1 ? matches[0] : undefined;
  if (respectEntry && entry.sid && !linked) return null;
  const chosen = linked ?? projects[0];
  return { key: chosen ? projectKey(chosen) : '', taskId: linked ? entry.taskId || '' : '' };
}

/** Consult the user cookie now: a cached identity cannot authorize a project link. */
export async function openMatchingUserProject(
  project: ProjectIdentity,
  readCurrentIdentity: () => Promise<WorkspaceIdentity>,
  navigate: (url: string) => void,
): Promise<{ matched: boolean; identity: WorkspaceIdentity }> {
  const identity = await readCurrentIdentity();
  const matched = identity.role === 'trial' && identity.key_id === project.tenant_id;
  if (matched) navigate(`/?project=${encodeURIComponent(project.sid)}`);
  return { matched, identity };
}
