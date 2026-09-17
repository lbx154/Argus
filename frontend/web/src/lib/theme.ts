import type { RenderTone } from '../../../core/src/eventRender';

/**
 * Restrained web workbench colours. Role hues are intentionally close in
 * chroma: labels stay distinguishable without turning the console into a
 * rainbow dashboard.
 */
export const theme = {
  accent: 'rgb(var(--blue))',
  success: 'rgb(var(--ok))',
  error: 'rgb(var(--err))',
  warning: 'rgb(var(--warn))',
  info: 'rgb(var(--blue))',
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

/** The colour a feed line's tone takes on this theme. */
export function toneColor(tone: RenderTone): string {
  switch (tone) {
    case 'bright': return theme.ink;
    case 'dim': return theme.inkDim;
    case 'accent': return theme.accent;
    case 'ok': return theme.success;
    case 'warn': return theme.warning;
    case 'err': return theme.error;
    case 'info': return theme.info;
  }
}

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
