/** Literal operator-facing copy. Status text reports observable state and
 * avoids anthropomorphic filler. */

/** Product copy stays operational and literal. */
export const TAGLINE = 'operator console';

/** Shown when a daemon is idle / the feed is quiet — reassuring, not empty.
 *  Identical to the terminal's IDLE_LINES so the two frontends read as one voice. */
export const IDLE_LINES = [
  'No active work.',
  'Event stream is idle.',
  'Ready for input.',
];

export {
  THINKING_LINES,
  SPINNER,
  rotateByTick,
  spinnerFrame,
} from '../../../core/src/thinking';

/** First line in a fresh daemon's chat — a warm, in-character welcome. */
export const WELCOME =
  'Ready. Send a message or assign work.';

/** A slowly-rotating index driven by a monotonic tick (for a live cycling line). */
export function rotate(lines: string[], tickMs = 3800): string {
  return lines[Math.floor(Date.now() / tickMs) % lines.length];
}
