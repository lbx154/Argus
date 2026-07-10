import React from 'react';
import { Box, Text } from 'ink';
import type { EventMsg } from '../api.js';
import { activityHistory } from '../../../core/src/activity.js';
import { theme } from '../theme.js';

const glyph = (status: string): string =>
  status === 'running' ? '●' : status === 'error' ? '✕' : '✓';

const duration = (seconds: number): string => {
  if (!seconds) return '';
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
};

/** Observable actions only: no prompts, token deltas, tool output, or chain-of-thought. */
export function ActivityPane({ events, max = 8 }: { events: EventMsg[]; max?: number }) {
  const rows = activityHistory(events, max);
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.border} paddingX={1} marginTop={1}>
      <Text dimColor>activity · Ctrl+O close · observable actions only</Text>
      {rows.length === 0 ? (
        <Text dimColor>  waiting for the next agent action…</Text>
      ) : rows.map((row) => {
        const bits = [row.model, row.detail, row.status === 'running' ? '' : duration(row.elapsedS)]
          .filter(Boolean)
          .join(' · ');
        return (
          <Box key={row.id}>
            <Text color={row.status === 'error' ? theme.error : theme.role[row.role] ?? 'white'}>
              {`  ${glyph(row.status)} ${row.role.padEnd(8)} `}
            </Text>
            <Text>{row.label}</Text>
            {bits ? <Text dimColor>{` · ${bits}`}</Text> : null}
          </Box>
        );
      })}
    </Box>
  );
}
