import type { ProjectCostRow, ProjectRow } from '../api';

export function mergeProjectCosts(
  projects: ProjectRow[],
  costs: ProjectCostRow[],
): ProjectRow[] {
  if (!costs.length) return projects;
  const byId = new Map(costs.map((row) => [row.id, row]));
  return projects.map((project) => {
    const cost = byId.get(project.id);
    if (!cost) return project;
    return {
      ...project,
      spend_usd: cost.spend_usd,
      known_cost_usd: cost.known_cost_usd,
      spend_status: cost.spend_status as ProjectRow['spend_status'],
      usage_calls: cost.usage_calls,
      premium_requests: cost.premium_requests,
      cost_updated_at: cost.updated_at,
    };
  });
}
