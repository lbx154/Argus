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

export function thinkingStatusLine(
  phase: string,
  tick: number,
  heartbeat = false,
  quietS = 0,
): string {
  const soulLine = `${rotateByTick(THINKING_LINES, tick)}…`;
  if (heartbeat) {
    const quiet = Math.max(0, Math.floor(Number.isFinite(quietS) ? quietS : 0));
    return `${soulLine} · Manager alive · ${quiet}s quiet`;
  }
  const raw = phase || soulLine;
  return raw.includes('[SESSION HANDOFF')
    ? 'Manager context refreshed · working on your message…'
    : raw.replace(/^Manager\s*·\s*/i, '').slice(0, 100);
}
