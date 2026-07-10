/**
 * Restrained web workbench colours. Role hues are intentionally close in
 * chroma: labels stay distinguishable without turning the console into a
 * rainbow dashboard.
 */
export const theme = {
  accent: '#c7a66a',
  success: '#7fa386',
  error: '#c77b72',
  warning: '#c1a363',
  info: '#8fa7b8',
  ink: '#efeee8',
  inkDim: '#b8b7af',
  inkFaint: '#7e7d75',
  role: {
    manager: '#90a8b5',
    planner: '#a69daf',
    engineer: '#8fa78f',
    reviewer: '#b5a57f',
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
