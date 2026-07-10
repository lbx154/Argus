import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { Daemon, Snapshot } from '../api.js';
import { Wordmark } from './Wordmark.js';
import { deriveMissionView, type MissionState } from '../../../core/src/mission.js';

const STATE_COLOR: Record<MissionState, string> = {
  working: theme.info,
  waiting: theme.warning,
  complete: theme.success,
  idle: 'gray',
  offline: theme.error,
};

const truncate = (text: string, max: number): string =>
  text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;

export function Header({
  snap,
  connected,
  width,
  health = '',
}: {
  snap: Snapshot | null;
  connected: boolean;
  width: number;
  health?: string;
}) {
  const name = snap?.session?.display_name || snap?.session?.id || '—';
  const d: Daemon | undefined = snap?.daemon;
  const mission = snap ? deriveMissionView(snap) : null;
  const link = connected
    ? { color: theme.success, text: 'live' }
    : { color: theme.warning, text: 'connecting…' };
  const compact = width < 78;
  return (
    <Box flexDirection="column">
      <Box>
        <Wordmark />
        <Text> </Text>
        <Text color="cyan">{truncate(name, compact ? 14 : 28)}</Text>
        <Text dimColor>{'  ·  '}</Text>
        <Text color={mission ? STATE_COLOR[mission.state] : 'gray'}>
          {mission ? mission.stateLabel : 'loading'}
        </Text>
        {!compact ? (
          <>
            <Text dimColor>{'  ·  '}</Text>
            <Text color={d?.alive ? theme.success : 'gray'}>{d?.alive ? 'daemon live' : 'daemon off'}</Text>
            <Text dimColor>{'  ·  '}</Text>
            <Text color={health ? theme.warning : link.color}>
              {health ? truncate(health, Math.max(16, width - 58)) : link.text}
            </Text>
          </>
        ) : null}
      </Box>
      {mission?.objective && width >= 90 ? (
        <Text dimColor>{`  ${truncate(mission.objective, width - 4)}`}</Text>
      ) : null}
      {compact && health ? (
        <Text color={theme.warning}>{`  ⚠ ${truncate(health, Math.max(12, width - 6))}`}</Text>
      ) : null}
    </Box>
  );
}
