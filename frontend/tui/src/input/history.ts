/**
 * Pure input history — up/down recall of previously submitted lines, with the
 * live draft preserved when you navigate away and restored when you come back.
 * ``pos`` = steps back from the live line (0 = live draft, 1 = most recent, …).
 */

export interface History {
  entries: string[]; // oldest → newest
  pos: number;
  draft: string; // the live line, saved when you first press Up
}

export const EMPTY_HISTORY: History = { entries: [], pos: 0, draft: '' };

/** Record a submitted line (non-empty, deduped against the immediately previous). */
export function remember(h: History, line: string): History {
  const t = line.trim();
  if (!t) return { entries: h.entries, pos: 0, draft: '' };
  const last = h.entries[h.entries.length - 1];
  const entries = last === t ? h.entries : [...h.entries, t];
  return { entries, pos: 0, draft: '' };
}

/** Up: recall an older line. */
export function older(h: History, current: string): { h: History; value: string } {
  if (h.entries.length === 0) return { h, value: current };
  const draft = h.pos === 0 ? current : h.draft;
  const pos = Math.min(h.pos + 1, h.entries.length);
  const value = pos === 0 ? draft : h.entries[h.entries.length - pos];
  return { h: { ...h, pos, draft }, value };
}

/** Down: move toward the live draft. */
export function newer(h: History): { h: History; value: string } {
  const pos = Math.max(h.pos - 1, 0);
  const value = pos === 0 ? h.draft : h.entries[h.entries.length - pos];
  return { h: { ...h, pos }, value };
}

/** True when the user is navigating history (so Up/Down should keep navigating
 *  rather than being ignored). */
export function isNavigating(h: History): boolean {
  return h.pos > 0;
}
