export const ARGUS_ROUNDED_ART_FULL = [
  '  ╭───────────────────────────────────────────────────────────────────────────────────╮╮',
  '  │                                                                                   ││',
  '  │   ◉  argus-skill  ·  Autonomous Research Lab                                     ││',
  '  │                                                                                   ││',
  '  ╰───────────────────────────────────────────────────────────────────────────────────╯│',
  '                                                                                        │',
] as const;

export const ARGUS_ROUNDED_ART_COMPACT = [
  '  ╭─────────────────────────────────╮╮',
  '  │   ◉  argus-skill               ││',
  '  ╰─────────────────────────────────╯│',
  '                                     │',
] as const;

export const ARGUS_SPLASH_COLORS = ['#3b6fd4', '#4d86e0', '#5f9deb', '#72b4f0', '#89dceb', '#cba6f7', '#e6b450'] as const;
export const ARGUS_SPLASH_ACTIVE_FRAMES = 17;
export const ARGUS_SPLASH_FADE_FRAMES = 5;
export const ARGUS_SPLASH_FRAME_MS = 80;
export const ARGUS_SPLASH_HOLD_MS = 120;

export const ARGUS_ROUNDED_ART_FULL_WIDTH = Math.max(...ARGUS_ROUNDED_ART_FULL.map((line) => [...line].length));

export function splashLogoForWidth(width: number): readonly string[] {
  return width >= ARGUS_ROUNDED_ART_FULL_WIDTH ? ARGUS_ROUNDED_ART_FULL : ARGUS_ROUNDED_ART_COMPACT;
}
