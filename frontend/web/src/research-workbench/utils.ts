export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

export type Tone = 'neutral' | 'info' | 'success' | 'warn' | 'danger' | 'live';
