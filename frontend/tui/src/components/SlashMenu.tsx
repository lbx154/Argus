import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { SlashCmd } from '../input/slash.js';

/** Slash-command completion dropdown, shown while typing a `/command` token. */
export function SlashMenu({ items, selected }: { items: SlashCmd[]; selected: number }) {
  if (items.length === 0) return null;
  return (
    <Box flexDirection="column" marginTop={1} marginLeft={1}>
      {items.map((c, i) => {
        const on = i === selected;
        return (
          <Box key={c.name}>
            <Text color={on ? theme.accent : undefined} bold={on}>
              {on ? '❯ ' : '  '}
              {c.name}
              {c.arg ? ` ${c.arg}` : ''}
            </Text>
            <Text dimColor>{`   ${c.desc}`}</Text>
          </Box>
        );
      })}
      <Text dimColor>{'  ↑↓ select · Tab complete · Esc dismiss'}</Text>
    </Box>
  );
}
