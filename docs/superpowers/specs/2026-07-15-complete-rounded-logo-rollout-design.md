# Complete Rounded 02 Logo Rollout

**Date:** 2026-07-15

## Goal

Remove every remaining legacy Argus logo treatment from the Web and Ink TUI
cockpits while preserving each medium's rendering constraints.

## Web

- Reuse `ArgusMark` and `Wordmark` as the only rendered brand primitives.
- Replace the Web `BootSplash` ASCII `ARGUS-SKILL` art with the real Rounded 02
  horizontal SVG on wide screens and the real Rounded 02 mark on narrow screens.
- Replace `frontend/web/public/favicon.svg` with Rounded 02 mark geometry:
  the outer `a` shape and circular eye/pupil used by `ArgusMark`.
- The favicon uses a fixed user-space blue-to-gold gradient. It must not depend
  on `currentColor`, CSS theme state, or page variables.
- Keep splash duration, click/key skip, fade timing, responsive breakpoint,
  reduced-motion behavior, and blue-gold theme unchanged.

## TUI

- Replace the legacy full and compact `ARGUS-SKILL` ASCII constants with
  Unicode block-art derived from the Rounded 02 `a + eye` silhouette.
- Rename exported splash constants to describe Rounded 02 terminal art; no
  runtime source may contain the old `ARGUS-SKILL` banner.
- Keep the existing per-character blue-to-gold shimmer, fade timing, terminal
  width fallback, and input-to-skip behavior.
- Change `Header` to render the shared TUI `Wordmark` instead of manually
  rendering `◆ ARGUS`.
- Evolve the shared TUI `Wordmark` from the unrelated diamond glyph to a compact
  terminal-safe Rounded 02 mark, followed by the lowercase `argus` gradient.
- `Splash`, `Header`, `ResumePicker`, `FirstRun`, connecting, and error states
  must all use either the shared wordmark or the same Rounded 02 terminal art.

## Tests and Acceptance

1. Web splash renders `data-logo="rounded-horizontal"` and
   `data-logo="rounded-mark"` and does not import ASCII splash constants.
2. Favicon geometry matches Rounded 02 and contains a fixed user-space
   blue-to-gold gradient.
3. TUI source contains no legacy `ARGUS-SKILL` banner and Header imports the
   shared `Wordmark`.
4. Full and compact terminal art both fit their selected width thresholds.
5. Web and TUI tests, typechecks, and release artifact fencing pass.
6. Only 8798 is restarted after deployment; the 8799 proxy remains unchanged.

