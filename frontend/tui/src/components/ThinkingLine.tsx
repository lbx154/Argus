import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { spinnerFrame, rotateByTick, THINKING_LINES } from '../soul.js';

/**
 * The live "Argus is thinking" line shown while a Manager turn is in flight —
 * before/between reply blocks. A spinner + elapsed seconds + either the real
 * phase ("Manager · reading events.jsonl") or a rotating soul line. This is the
 * single biggest cure for the "frozen screen" feel: the operator always sees
 * Argus is alive and working, never a dead terminal.
 */
export function ThinkingLine({ tick, phase, elapsedS }: { tick: number; phase: string; elapsedS: number }) {
  const spin = spinnerFrame(tick);
  const raw = phase || `${rotateByTick(THINKING_LINES, tick)}…`;
  const body = raw.includes('[SESSION HANDOFF')
    ? 'Manager context refreshed · working on your message…'
    : raw.replace(/^Manager\s*·\s*/i, '').slice(0, 100);
  return (
    <Box flexDirection="column" marginTop={1}>
      <Text wrap="truncate-end">
        {'  '}
        <Text color={theme.role.manager ?? 'magenta'}>{spin}</Text>
        {' '}
        <Text color={theme.role.manager ?? 'magenta'} bold>Your message</Text>
        {'  '}
        <Text color={theme.accent}>{body}</Text>
        <Text dimColor>{`   ${elapsedS}s`}</Text>
      </Text>
      <Text dimColor>{'  Esc stop waiting · /cancel'}</Text>
    </Box>
  );
}
