import type { Project, RoleRuntime } from './store.js';
import { ROLES, type Role } from './types.js';

/** Resolve a full route, never borrowing a model from a different CLI. */
export function roleRuntime(project: Project, role: Role): RoleRuntime {
  const route = project.roleConfig?.[role] ?? project;
  return { backend: route.backend, model: route.model,
    modelAutomatic: route.modelAutomatic ?? !route.model,
    ...(route.effort ? { effort: route.effort } : {}) };
}

export const routeKey = (route: RoleRuntime): string =>
  JSON.stringify([route.backend, route.model, route.effort ?? '', route.modelAutomatic ?? !route.model]);

/** Apply prevalidated routes atomically. A call already running keeps its captured route. */
export function applyRoleRuntimes(project: Project, updates: Partial<Record<Role, RoleRuntime>>): void {
  // Freeze the legacy baseline for every role before changing the Manager compatibility mirror.
  project.roleConfig = Object.fromEntries(ROLES.map(role => [role, roleRuntime(project, role)]));
  for (const role of ROLES) {
    const next = updates[role];
    if (!next) continue;
    const previous = roleRuntime(project, role);
    if (routeKey(previous) === routeKey(next)) continue;
    (project.roleConfig ??= {})[role] = { ...next };
    if (previous.backend !== next.backend || previous.model !== next.model) {
      (project.roleEpochs ??= {})[role] = (project.roleEpochs[role] ?? 0) + 1;
      for (const key of Object.keys(project.sessions)) if (key === role || key.endsWith(`.${role}`)) delete project.sessions[key];
    }
  }
  const manager = roleRuntime(project, 'manager');
  project.backend = manager.backend;
  project.model = manager.model;
  project.modelAutomatic = manager.modelAutomatic ?? !manager.model;
  if (manager.effort) project.effort = manager.effort; else delete project.effort;
  project.configRevision = (project.configRevision ?? 0) + 1;
}

/** Pin one observed automatic selection; top-level fields remain a legacy Manager mirror. */
export function pinRoleModel(project: Project, role: Role, model: string): void {
  project.roleConfig = Object.fromEntries(ROLES.map(role => [role, roleRuntime(project, role)]));
  project.roleConfig[role] = { ...roleRuntime(project, role), model, modelAutomatic: true };
  if (role === 'manager') { project.model = model; project.modelAutomatic = true; }
}

/** Automatic selections are resolved anew in the new project, not copied as pinned models. */
export function inheritedRoleRuntimes(project: Project): Partial<Record<Role, RoleRuntime>> | undefined {
  if (!project.roleConfig) return undefined;
  return Object.fromEntries(ROLES.map(role => {
    const route = roleRuntime(project, role);
    return [role, { ...route, model: route.modelAutomatic ? null : route.model }];
  }));
}
