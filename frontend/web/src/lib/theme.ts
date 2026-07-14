/**
 * Restrained web workbench colours. Role hues are intentionally close in
 * chroma: labels stay distinguishable without turning the console into a
 * rainbow dashboard.
 */
export const theme = {
  accent: 'rgb(var(--spectral-gold))',
  success: '#7fa386',
  error: '#c77b72',
  warning: 'rgb(var(--spectral-gold))',
  info: 'rgb(var(--spectral-blue))',
  ink: 'rgb(var(--ink))',
  inkDim: 'rgb(var(--ink-dim))',
  inkFaint: 'rgb(var(--ink-faint))',
  role: {
    manager: 'rgb(var(--role-manager))',
    planner: 'rgb(var(--role-planner))',
    engineer: 'rgb(var(--role-engineer))',
    reviewer: 'rgb(var(--role-reviewer))',
  } as Record<string, string>,
};

/** Reasoning effort is metadata, not a heat-map. */
export function effortColor(effort: string | null | undefined): string {
  switch (effort) {
    case 'medium':
      return theme.inkDim;
    case 'high':
      return theme.info;
    case 'xhigh':
      return theme.accent;
    case 'max':
      return theme.error;
    default:
      return theme.inkFaint;
  }
}
