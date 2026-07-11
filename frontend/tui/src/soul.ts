/**
 * Argus's soul, terminal edition — the twin of web's ``lib/soul.ts``. Argus
 * Panoptes, the hundred-eyed giant who never fully slept: some eyes always
 * stayed open. That IS this product — a sleepless researcher keeping watch 24/7.
 * The voice is calm, a little mythic; never cutesy, never corporate. Used for
 * idle/empty states and the "thinking" indicator that un-freezes the CLI.
 */

/** The one-line identity under the wordmark. */
export const TAGLINE = 'a hundred eyes on your research — some never close';

/** First line in a fresh cockpit — a warm, in-character welcome. Kept in sync
 *  with web's WELCOME; exported here for parity even though no CLI surface
 *  currently renders a one-shot chat welcome (see soul.ts's sibling doc). */
export const WELCOME =
  'A fresh set of eyes, open and watching. Tell me what to look into — or just say hi.';

/** Shown when the feed is quiet — reassuring, not empty. Identical to web's
 *  IDLE_LINES so the two frontends read as one voice. */
export const IDLE_LINES = [
  'All quiet. Argus keeps watch.',
  'Nothing stirring — some eyes stay open.',
  'Standing watch. Say the word.',
  'Resting, not sleeping.',
  'Quiet stretch. The watch continues.',
  'No signal yet — Argus doesn\'t blink first.',
  'The feed is calm. So is Argus.',
  'Waiting, unhurried.',
];

export {
  THINKING_LINES,
  SPINNER,
  rotateByTick,
  spinnerFrame,
} from '../../core/src/thinking.js';

/** Pick from ``lines`` by wall-clock time — the twin of web's ``rotate``, for
 *  components (like the idle empty-state) that have no tick of their own and
 *  just want a slowly-changing line across re-renders. */
export function rotate(lines: string[], tickMs = 3800): string {
  if (lines.length === 0) return '';
  return lines[Math.floor(Date.now() / tickMs) % lines.length];
}
