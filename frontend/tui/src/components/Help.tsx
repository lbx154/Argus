import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { SLASH_COMMANDS } from '../input/slash.js';

const KEYS: Array<[string, string]> = [
  ['Enter', 'send message'],
  ['← → / Ctrl-A / Ctrl-E', 'move caret · home · end'],
  ['Ctrl-W / Ctrl-U / Ctrl-K', 'delete word · to start · to end'],
  ['↑ ↓', 'input history'],
  ['/', 'slash commands (autocompletes)'],
  ['? · /help', 'this help'],
  ['Esc · /cancel', 'stop waiting for Manager reply'],
  ['Ctrl-C', 'quit (daemon keeps running)'],
];

/** The `?` / `/help` overlay: keybindings + the slash-command reference. */
export function Help() {
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.border} paddingX={2} paddingY={0} marginTop={1}>
      <Text bold color={theme.accent}>
        argus cockpit — keys
      </Text>
      {KEYS.map(([k, d]) => (
        <Box key={k}>
          <Text color="cyan">{k.padEnd(26)}</Text>
          <Text dimColor>{d}</Text>
        </Box>
      ))}
      <Text> </Text>
      <Text bold color={theme.accent}>
        commands
      </Text>
      {SLASH_COMMANDS.map((c) => (
        <Box key={c.name}>
          <Text color="cyan">{`${c.name}${c.arg ? ` ${c.arg}` : ''}`.padEnd(26)}</Text>
          <Text dimColor>{c.desc}</Text>
        </Box>
      ))}
      <Text> </Text>
      <Text dimColor>press any key to close</Text>
    </Box>
  );
}
