import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { spinnerFrame } from '../soul.js';
import { thinkingStatusLine } from '../../../core/src/thinking.js';

/**
 * The live "Argus is thinking" line shown while a Manager turn is in flight —
 * before/between reply blocks. A spinner + elapsed seconds + either the real
 * phase ("Manager · reading events.jsonl") or a rotating soul line. This is the
 * single biggest cure for the "frozen screen" feel: the operator always sees
 * Argus is alive and working, never a dead terminal.
 */
export function ThinkingLine({
  tick,
  phase,
  elapsedS,
  heartbeat = false,
  quietS = 0,
}: {
  tick: number;
  phase: string;
  elapsedS: number;
  heartbeat?: boolean;
  quietS?: number;
}) {
  const spin = spinnerFrame(tick);
  const body = thinkingStatusLine(phase, tick, heartbeat, quietS);
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
