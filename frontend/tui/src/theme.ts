/**
 * Visual DNA lifted from Claude Code (src/utils/theme.ts) — hex only, no code.
 * Role hues match the Python cockpit (cli/roles_status.py ROLE_COLOR) so the
 * TUI and the Rich line-cockpit read the same: manager=cyan, planner=magenta,
 * engineer=green, reviewer=yellow.
 */
/**
 * Palette — blue tones + a gold accent. The wordmark rides a royal-blue→sky
 * gradient; the diamond, input glyph and other accents are gold, so "◆ argus"
 * reads as a gold mark on a blue wordmark.
 */
export const theme = {
  accent: '#e6b450', // gold
  border: '#8a93a6', // cool gray-blue
  success: '#3aa76a', // green (semantic — kept)
  error: '#d15c6a', // red (semantic — kept)
  warning: '#d0a850', // amber-gold
  info: '#5a9beb', // blue
  role: {
    manager: 'blue',
    planner: 'magenta',
    engineer: 'green',
    reviewer: 'yellow',
  } as Record<string, string>,
};

/** Braille spinner frames — same cadence as the Python cockpit / Codex CLI. */
export const SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

/**
 * Wordmark gradient — a royal-blue → sky ramp across the 5 letters of "argus",
 * coloured by ABSOLUTE index (a=deep blue … s=sky). Shared by the boot Splash
 * and the Header so the handoff has no colour pop. GHOST = a letter not yet lit;
 * SHIMMER = the gold glint; TAG = the dim tagline hue.
 */
export const WORDMARK_RAMP = ['#3b6fd4', '#4d86e0', '#5f9deb', '#72b4f0', '#89dceb'];
export const WORDMARK_GHOST = '#48506b';
export const WORDMARK_SHIMMER = '#f4e0a8'; // warm gold glint (was near-white)
export const WORDMARK_TAG = '#6c7086';

/** Reasoning-effort → colour (blue/gold scale): low=dim, medium=blue,
 * high=amber-gold, xhigh=gold, max=red. */
export function effortColor(effort: string | null | undefined): string {
  switch (effort) {
    case 'medium':
      return theme.info;
    case 'high':
      return theme.warning;
    case 'xhigh':
      return theme.accent; // gold
    case 'max':
      return theme.error;
    default:
      return 'gray'; // low / none
  }
}
