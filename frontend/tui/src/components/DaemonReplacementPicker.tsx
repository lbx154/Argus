import React from 'react';
import { Box, Text } from 'ink';
import type { ProjectRow } from '../api.js';
import { theme } from '../theme.js';

export interface DaemonReplacementState {
  targetProject: string;
  running: ProjectRow[];
  limit: number;
  activeCount: number;
  selection: number;
  resumeContinuous: boolean;
  busy: boolean;
  error: string;
}

const clip = (value: string, max: number): string =>
  value.length <= max ? value : `${value.slice(0, Math.max(1, max - 1))}…`;

export function DaemonReplacementPicker({
  state,
  width,
}: {
  state: DaemonReplacementState;
  width: number;
}) {
  const textWidth = Math.max(20, width - 12);
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.warning}
      paddingX={2}
      marginTop={1}
    >
      <Text bold color={theme.warning}>
        {`Concurrent work limit reached · ${state.activeCount}/${state.limit}`}
      </Text>
      <Text dimColor>
        Choose one running session to park. Its files, backlog, checkpoints, skills, and wiki stay saved.
      </Text>
      <Box flexDirection="column" marginTop={1}>
        {state.running.map((row, index) => {
          const selected = index === state.selection;
          const title = row.label || row.display_name || row.id;
          const work = row.activity || row.current_task || row.continuous_objective || 'standing by';
          return (
            <Box key={row.id} flexDirection="column">
              <Box>
                <Text color={selected ? theme.accent : 'gray'}>{selected ? '› ' : '  '}</Text>
                <Text color={row.daemon_alive ? theme.success : 'gray'}>{row.daemon_alive ? '● ' : '○ '}</Text>
                <Text bold={selected} color={selected ? theme.accent : undefined}>
                  {clip(title, Math.max(12, textWidth - 24))}
                </Text>
                <Text dimColor>{`  ${row.id}  pid ${row.daemon_pid ?? '—'}`}</Text>
              </Box>
              <Text dimColor>{`    ${clip(work, textWidth)}`}</Text>
            </Box>
          );
        })}
      </Box>
      <Box marginTop={1}>
        {state.error ? (
          <Text color={theme.error}>{state.error}</Text>
        ) : state.busy ? (
          <Text color={theme.accent}>Parking selected session and starting queued work…</Text>
        ) : (
          <Text dimColor>↑/↓ select · Enter park & replace · Esc leave new work queued</Text>
        )}
      </Box>
    </Box>
  );
}
