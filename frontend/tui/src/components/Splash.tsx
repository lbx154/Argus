import React, { useEffect, useRef, useState } from 'react';
import { Box, Text, useInput, useStdout } from 'ink';

const LOGO_FULL = [
  ' █████╗ ██████╗  ██████╗ ██╗   ██╗███████╗      ███████╗██╗  ██╗██╗██╗     ██╗',
  '██╔══██╗██╔══██╗██╔════╝ ██║   ██║██╔════╝      ██╔════╝██║ ██╔╝██║██║     ██║',
  '███████║██████╔╝██║  ███╗██║   ██║███████╗█████╗███████╗█████╔╝ ██║██║     ██║',
  '██╔══██║██╔══██╗██║   ██║██║   ██║╚════██║╚════╝╚════██║██╔═██╗ ██║██║     ██║',
  '██║  ██║██║  ██║╚██████╔╝╚██████╔╝███████║      ███████║██║  ██╗██║███████╗███████╗',
  '╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚══════╝      ╚══════╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝',
] as const;

const LOGO_COMPACT = [
  '                              _   _ _ _',
  ' __ _ _ _ __ _ _  _ ______ __| |_(_) | |',
  "/ _` | '_/ _` | || (_-<___(_-< / / | | |",
  '\\__,_|_| \\__, |\\_,_/__/   /__/_\\_\\_|_|_|',
  '         |___/',
] as const;

const COLORS = ['#3b6fd4', '#4d86e0', '#5f9deb', '#72b4f0', '#89dceb', '#cba6f7', '#e6b450'];
const ACTIVE_FRAMES = 17;
const FADE_FRAMES = 5;
const FRAME_MS = 80;
const HOLD_MS = 120;
const FULL_WIDTH = Math.max(...LOGO_FULL.map((line) => [...line].length));

export function splashLogoForWidth(width: number): readonly string[] {
  return width >= FULL_WIDTH ? LOGO_FULL : LOGO_COMPACT;
}

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
