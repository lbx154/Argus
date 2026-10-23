import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { Wordmark } from './Wordmark.js';
import { applicationBrand } from '../applicationBrand.js';

const truncate = (text: string, max: number): string =>
  text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;

export function Header({
  width,
  health = '',
  vertical = '',
}: {
  width: number;
  health?: string;
  vertical?: string;
}) {
  return (
    <Box flexDirection="column">
      <Box>
        <Wordmark />
        <Text dimColor>{` · ${applicationBrand().id === 'argus' && vertical === 'research' ? 'Autonomous Research Lab' : applicationBrand().tagline}`}</Text>
      </Box>
      {health ? (
        <Text color={theme.warning}>{`  ! ${truncate(health, Math.max(12, width - 6))}`}</Text>
      ) : null}
    </Box>
  );
}
