import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { spinnerFrame } from '../soul.js';

/** The reply is under way; real role messages are in the project timeline. */
export function ThinkingLine({ tick, elapsedS }: { tick: number; elapsedS: number }) {
  return <Box flexDirection="column" marginTop={1}>
    <Text wrap="truncate-end">{'  '}<Text color={theme.role.manager ?? 'magenta'}>{spinnerFrame(tick)}</Text>{' '}
      <Text color={theme.role.manager ?? 'magenta'} bold>Your message</Text>{'  '}
      <Text color={theme.accent}>Replying to your message</Text><Text dimColor>{`   ${elapsedS}s`}</Text>
    </Text>
    <Text dimColor>{'  Esc stop waiting · /cancel'}</Text>
  </Box>;
}
