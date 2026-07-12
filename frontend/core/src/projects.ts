import type { ProjectRow } from './types.js';

export function hasHumanProjectLabel(project: ProjectRow): boolean {
  const label = (project.label || project.display_name || '').trim();
  return Boolean(label && label !== project.id);
}

/** Live work first, then human-labelled work, then recency. */
export function rankProjects(projects: ProjectRow[]): ProjectRow[] {
  return [...projects].sort((a, b) => {
    if (a.daemon_alive !== b.daemon_alive) return a.daemon_alive ? -1 : 1;
    const aNamed = hasHumanProjectLabel(a);
    const bNamed = hasHumanProjectLabel(b);
    if (aNamed !== bNamed) return aNamed ? -1 : 1;
    return (b.last_active || 0) - (a.last_active || 0);
  });
}

export function defaultProject(projects: ProjectRow[]): ProjectRow | undefined {
  return rankProjects(projects)[0];
}

export interface ProjectSelection {
  id: string | null;
  requested: string | null;
  /** True when a non-empty requested ID did not exist in the project list. */
  recovered: boolean;
}

/** Validate a deep-link/CLI project ID and recover to the shared best project. */
export function resolveProjectSelection(
  projects: ProjectRow[],
  requested?: string | null,
): ProjectSelection {
  const wanted = requested?.trim() || null;
  if (wanted && projects.some((project) => project.id === wanted)) {
    return { id: wanted, requested: wanted, recovered: false };
  }
  return {
    id: defaultProject(projects)?.id ?? null,
    requested: wanted,
    recovered: Boolean(wanted),
  };
}

/** Polling-safe selection: only the first authoritative list may choose a default. */
export function reconcileProjectSelection(
  projects: ProjectRow[],
  current: string | null,
  initialized: boolean,
): ProjectSelection {
  if (initialized) {
    const id = current?.trim() || null;
    return { id, requested: id, recovered: false };
  }
  return resolveProjectSelection(projects, current);
}

/** Multi-term project lookup shared by Web and Ink navigation surfaces. */
export function projectMatchesQuery(project: ProjectRow, query: string): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const status = project.daemon_alive ? 'live running' : 'stopped idle';
  const text = [
    project.id,
    project.label,
    project.display_name,
    project.objective,
    status,
  ].filter(Boolean).join(' ').toLowerCase();
  return terms.every((term) => text.includes(term));
}

export function filterProjects(projects: ProjectRow[], query: string): ProjectRow[] {
  return projects.filter((project) => projectMatchesQuery(project, query));
}

function normalizedPath(value: string): string {
  return value.replace(/\/+$/, '') || '/';
}

/** Scope resume results strictly to the directory where the session was launched. */
export function projectsForLaunchCwd(
  projects: ProjectRow[],
  cwd: string,
  includeAll = false,
): ProjectRow[] {
  if (includeAll) return projects;
  const root = normalizedPath(cwd);
  return projects.filter((project) => {
    const launch = (project.launch_cwd || '').trim();
    if (launch) {
      const candidate = normalizedPath(launch);
      return candidate === root || candidate.startsWith(`${root}/`);
    }
    const legacyCwd = (project.cwd || '').trim();
    if (!legacyCwd || legacyCwd.includes('/.argus-skill/projects/')) return false;
    const candidate = normalizedPath(legacyCwd);
    return candidate === root || candidate.startsWith(`${root}/`);
  });
}
