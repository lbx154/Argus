import { theme } from './theme';

/** Shared presentation only: the recorded role IDs remain unchanged. */
export const AGENT_ROLES = ['manager', 'planner', 'engineer', 'reviewer'] as const;
export type AgentRole = typeof AGENT_ROLES[number];
type Translate = (key: string) => string;

export function isAgentRole(role: string): role is AgentRole {
  return AGENT_ROLES.some(candidate => candidate === role);
}

export function agentRoleName(role: AgentRole, t: Translate): string {
  return t(`role.${role}`);
}

export function agentRoleDescription(role: AgentRole, t: Translate): string {
  return t(`role.${role}.description`);
}

export function agentRoleColor(role: string): string {
  return theme.role[role] ?? theme.inkFaint;
}
