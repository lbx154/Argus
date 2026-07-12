export const THINKING_LINES = [
  'turning it over',
  'consulting a hundred eyes',
  'reading the room',
  'weighing it',
  'thinking it through',
  'cross-checking the evidence',
  'running the numbers',
  'sizing up the angles',
  'following the thread',
  'letting it settle',
] as const;

export const SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'] as const;

export function rotateByTick(lines: readonly string[], tick: number, every = 20): string {
  if (lines.length === 0) return '';
  return lines[Math.floor(tick / every) % lines.length];
}

export function spinnerFrame(tick: number): string {
  return SPINNER[tick % SPINNER.length];
}
