import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { GuardianAlert } from '../guardian.js';

/**
 * The proactive guardian banner (监视守护) — a can't-miss line the cockpit pins
 * when the daemon raised an alert for the operator (a hard block, a reviewer
 * backend failure, a budget pause, a stall). Argus Panoptes doesn't just log the
 * problem in the scroll and move on; it holds it in front of you until the work
 * moves on. Cleared automatically the moment the mission resumes.
 */
export function GuardianBanner({ alert }: { alert: GuardianAlert | null }) {
  if (!alert) return null;
  const block = alert.tone === 'block';
  const color = block ? theme.error : theme.warning;
  const glyph = block ? '⛔' : '👁';
  const kind = block ? 'NEEDS YOU' : 'WATCHING';
  return (
    <Box marginTop={1} borderStyle="round" borderColor={color} paddingX={1}>
      <Text color={color} bold>
        {glyph} {kind}
      </Text>
      <Text>{'  '}</Text>
      <Text color={color}>{alert.text}</Text>
    </Box>
  );
}
