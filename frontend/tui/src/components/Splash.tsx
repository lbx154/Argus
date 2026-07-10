import React, { useEffect, useRef, useState } from 'react';
import { Box, Text, useInput } from 'ink';
import { WORDMARK_TAG } from '../theme.js';
import { Wordmark } from './Wordmark.js';
import { TAGLINE } from '../soul.js';

/**
 * "Panoptes Ignition" boot splash. The ◆ ignites (the all-seeing eye opening /
 * locking on — one warm accent pulse), then "argus" materializes left→right out
 * of dim ghost into its mauve→blue→teal gradient, sealed by one white shimmer
 * sweep, then a dim tagline fades in. ~1.6s, then hands off to the cockpit.
 *
 * Every visual is derived from one integer frame index — no stringly-typed
 * frames. The resting frame draws the same <Wordmark/> the Header draws, so the
 * handoff is a visual no-op. Any keypress skips instantly.
 */

interface Fr {
  d: 'ghost' | 'flick' | 'solid';
  lit: number;
  sh: number;
  tag: number;
}

const FRAMES: Fr[] = [
  { d: 'ghost', lit: -1, sh: -1, tag: 0 }, // f0  eye closed
  { d: 'flick', lit: -1, sh: -1, tag: 0 }, // f1  flicker
  { d: 'solid', lit: -1, sh: -1, tag: 0 }, // f2  ignite (lock-on pulse)
  { d: 'solid', lit: 0, sh: -1, tag: 0 }, // f3  word ghosts in
  { d: 'solid', lit: 1, sh: -1, tag: 0 }, // f4  ┐
  { d: 'solid', lit: 2, sh: -1, tag: 0 }, // f5  │ gradient wipe
  { d: 'solid', lit: 3, sh: -1, tag: 0 }, // f6  │ left → right
  { d: 'solid', lit: 4, sh: -1, tag: 0 }, // f7  │
  { d: 'solid', lit: 5, sh: -1, tag: 0 }, // f8  ┘ fully lit
  { d: 'solid', lit: 5, sh: 0, tag: 0 }, // f9   ┐
  { d: 'solid', lit: 5, sh: 1, tag: 0 }, // f10  │ shimmer sweep
  { d: 'solid', lit: 5, sh: 2, tag: 0 }, // f11  │
  { d: 'solid', lit: 5, sh: 3, tag: 0 }, // f12  │
  { d: 'solid', lit: 5, sh: 4, tag: 0 }, // f13  ┘
  { d: 'solid', lit: 5, sh: -1, tag: 1 }, // f14  tagline fades in
  { d: 'solid', lit: 5, sh: -1, tag: 2 }, // f15  tagline full
  { d: 'solid', lit: 5, sh: -1, tag: 2 }, // f16  rest (held, then handoff)
];

// Same one-line identity soul.ts feeds the web header's Wordmark tag — CLI and
// web read the same tagline.
const TAG = `  ${TAGLINE}`;
const FRAME_MS = 80;
const HOLD_MS = 240;

export function Splash({ onDone }: { onDone: () => void }) {
  const [i, setI] = useState(0);
  const finished = useRef(false);

  const finish = () => {
    if (finished.current) return;
    finished.current = true;
    onDone();
  };

  useInput(() => finish()); // any key skips to the cockpit

  useEffect(() => {
    const id = setInterval(() => {
      setI((x) => {
        if (x >= FRAMES.length - 1) {
          clearInterval(id);
          setTimeout(finish, HOLD_MS);
          return x;
        }
        return x + 1;
      });
    }, FRAME_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const f = FRAMES[i];
  return (
    <Box flexDirection="column" paddingX={1}>
      <Wordmark d={f.d} lit={f.lit} sh={f.sh} />
      {f.tag > 0 ? (
        <Text color={WORDMARK_TAG} dimColor={f.tag < 2}>
          {TAG}
        </Text>
      ) : null}
    </Box>
  );
}
