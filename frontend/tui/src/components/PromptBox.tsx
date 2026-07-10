import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { caretSplit, type Edit } from '../input/editor.js';

/**
 * Rounded input with a real caret. The editor retains the complete multiline
 * paste, while this component renders a bounded cursor window (newlines as ↵)
 * so a long prompt never takes over the terminal.
 */
export function PromptBox({ edit, width = 80 }: { edit: Edit; width?: number }) {
  const chars = Array.from(edit.value);
  const budget = Math.max(8, width - 32);
  let start = Math.max(0, edit.cursor - budget + 1);
  let end = Math.min(chars.length, start + budget);
  if (end - start < budget) start = Math.max(0, end - budget);
  const visible = chars.slice(start, end).map((char) => {
    if (char === '\n') return '↵';
    if (char === '\t') return '⇥';
    return char;
  }).join('');
  const localCursor = Math.max(0, Math.min(edit.cursor - start, Array.from(visible).length));
  const { before, at, after } = caretSplit({ value: visible, cursor: localCursor });
  const clipped = start > 0 || end < chars.length;
  return (
    <Box
      borderStyle="round"
      borderColor={theme.border}
      paddingX={1}
      marginTop={1}
      width="100%"
      height={3}
      overflow="hidden"
    >
      <Text color={theme.accent}>◆ </Text>
      <Text dimColor>talk to Argus › </Text>
      {start > 0 ? <Text dimColor>…</Text> : null}
      <Text wrap="truncate-end">{before}</Text>
      {at ? <Text inverse>{at}</Text> : <Text color={theme.accent}>▏</Text>}
      <Text wrap="truncate-end">{after}</Text>
      {end < chars.length ? <Text dimColor>…</Text> : null}
      {clipped ? <Text dimColor>{` ·${chars.length}`}</Text> : null}
    </Box>
  );
}
