import React, { useEffect, useRef, useState } from 'react';
import { Box, Text, useInput, useStdout } from 'ink';
import {
  ARGUS_SPLASH_ACTIVE_FRAMES as ACTIVE_FRAMES,
  ARGUS_SPLASH_COLORS as COLORS,
  ARGUS_SPLASH_FADE_FRAMES as FADE_FRAMES,
  ARGUS_SPLASH_FRAME_MS as FRAME_MS,
  ARGUS_SPLASH_HOLD_MS as HOLD_MS,
  splashLogoForWidth,
} from '../../../core/src/splash.js';

export {
  ARGUS_ROUNDED_ART_COMPACT,
  ARGUS_ROUNDED_ART_FULL,
  splashLogoForWidth,
} from '../../../core/src/splash.js';

function AnimatedLine({
  line,
  row,
  frame,
  dim,
}: {
  line: string;
  row: number;
  frame: number;
  dim: boolean;
}) {
  return (
    <Text dimColor={dim}>
      {[...line].map((char, column) => (
        <Text key={column} color={COLORS[(Math.floor(column / 7) + row + frame) % COLORS.length]}>
          {char}
        </Text>
      ))}
    </Text>
  );
}

export function Splash({ onDone }: { onDone: () => void }) {
  const { stdout } = useStdout();
  const logo = splashLogoForWidth(stdout.columns ?? 80);
  const [frame, setFrame] = useState(0);
  const finished = useRef(false);

  const finish = () => {
    if (finished.current) return;
    finished.current = true;
    onDone();
  };

  useInput(finish);

  useEffect(() => {
    const timer = setInterval(() => {
      setFrame((current) => {
        if (current < ACTIVE_FRAMES + FADE_FRAMES - 1) return current + 1;
        clearInterval(timer);
        setTimeout(finish, HOLD_MS);
        return current;
      });
    }, FRAME_MS);
    return () => clearInterval(timer);
    // finish is intentionally stable for the lifetime of this splash.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fadeStep = Math.max(0, frame - ACTIVE_FRAMES + 1);
  const hiddenRows = fadeStep <= 1 ? 0 : Math.min(logo.length, (fadeStep - 1) * 2);
  const firstVisible = Math.floor(hiddenRows / 2);
  const lastVisible = logo.length - Math.ceil(hiddenRows / 2);

  return (
    <Box flexDirection="column" paddingX={1}>
      {logo.map((line, row) => (
        <AnimatedLine
          key={row}
          line={row >= firstVisible && row < lastVisible ? line : ' '.repeat([...line].length)}
          row={row}
          frame={frame}
          dim={fadeStep > 0}
        />
      ))}
    </Box>
  );
}
