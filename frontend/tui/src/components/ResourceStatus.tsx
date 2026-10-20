import React from 'react';
import { Box, Text } from 'ink';
import type { ResourceStatus } from '../../../core/src/resourceStatus.generated.js';
import { theme } from '../theme.js';

function probeColor(status: ResourceStatus['accelerators'][number]['status']): string {
  if (status === 'available') return theme.success;
  if (status === 'absent') return 'gray';
  return theme.warning;
}

export function ResourceStatusView({ status }: { status: ResourceStatus }) {
  return (
    <Box flexDirection="column">
      <Box>
        <Text dimColor>{'resources'.padEnd(14)}</Text>
        <Text color={status.enforcement === 'strict' ? theme.success : theme.warning}>
          {status.enforcement} mode
        </Text>
      </Box>
      {status.accelerators.map((accelerator) => (
        <Box key={accelerator.kind} flexDirection="column">
          <Text>
            <Text dimColor>{`  ${accelerator.kind.toUpperCase()}`.padEnd(14)}</Text>
            <Text color={probeColor(accelerator.status)}>{accelerator.status}</Text>
            <Text dimColor>{` · ${accelerator.device_count} devices`}</Text>
          </Text>
          {accelerator.detail ? <Text dimColor>{`    ${accelerator.detail}`}</Text> : null}
        </Box>
      ))}
    </Box>
  );
}
