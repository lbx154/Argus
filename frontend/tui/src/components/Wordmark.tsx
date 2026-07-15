import React from 'react';
import { Text } from 'ink';
import { theme, WORDMARK_GHOST, WORDMARK_RAMP, WORDMARK_SHIMMER } from '../theme.js';

const WORD = 'argus';

/**
 * The shared "◆ argus" gradient wordmark, reused by the boot Splash and the
 * Header so the animation's resting frame is byte-identical to the live header
 * (no colour pop at handoff).
 *
 * - d: diamond state — 'ghost' (hollow dim ◇), 'flick' (dim ◆), 'solid' (◆ bold accent).
 * - lit: how many letters are lit in their ramp hue (0..5); the rest are ghost.
 *        -1 hides the word entirely (diamond-only frames).
 * - sh: index (0..4) of the letter currently carrying the white shimmer, or -1.
 */
export function Wordmark({
  d = 'solid',
  lit = WORD.length,
  sh = -1,
}: {
  d?: 'ghost' | 'flick' | 'solid';
  lit?: number;
  sh?: number;
}) {
  const markColor = d === 'ghost' ? WORDMARK_GHOST : theme.accent;
  return (
    <Text>
      <Text color={markColor} bold={d === 'solid'} dimColor={d === 'flick'}>
        {'◉'}
      </Text>
      {lit >= 0 ? (
        <>
          <Text> </Text>
          {[...WORD].map((ch, i) => {
            const isShimmer = i === sh;
            const color = isShimmer ? WORDMARK_SHIMMER : i < lit ? WORDMARK_RAMP[i] : WORDMARK_GHOST;
            return (
              <Text key={i} color={color} bold={isShimmer}>
                {ch}
              </Text>
            );
          })}
        </>
      ) : null}
    </Text>
  );
}
