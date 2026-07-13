import React from 'react';
import { Box, Static, Text } from 'ink';
import { theme } from '../theme.js';

const truncate = (text: string, max: number): string =>
  text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;

export type HeaderStaticItem = { id: string };

export const HEADER_STATIC_ITEMS: HeaderStaticItem[] = [
  { id: 'argus-header' },
];

export function Header({
  width,
  health = '',
}: {
  width: number;
  health?: string;
}) {
  return (
    <Box flexDirection="column">
      <Box>
        <Text color={theme.accent}>◆ </Text>
        <Text color={theme.info} bold>ARGUS</Text>
        <Text dimColor> · Autonomous Research Lab</Text>
      </Box>
      {health ? (
        <Text color={theme.warning}>{`  ! ${truncate(health, Math.max(12, width - 6))}`}</Text>
      ) : null}
    </Box>
  );
}

export function StaticHeader({ width }: { width: number }) {
  return (
    <Static items={HEADER_STATIC_ITEMS}>
      {(item) => <Header key={item.id} width={width} />}
    </Static>
  );
}
