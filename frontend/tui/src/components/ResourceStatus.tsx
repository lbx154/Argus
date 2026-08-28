import React from 'react';
import { Box, Text } from 'ink';
import type { ResourceStatus } from '../../../core/src/resourceStatus.generated.js';
import { formatResourceTtl } from '../../../core/src/resourceStatus.js';
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
      <Text> </Text>
      <Text dimColor>{`holders · ${status.holders.length}`}</Text>
      {status.holders.length === 0 ? <Text dimColor>  (none)</Text> : status.holders.map((holder, index) => (
        <Box key={`${holder.project}:${holder.task_id}:${index}`} flexDirection="column">
          <Text>
            <Text>{`  ${holder.project} · ${holder.task_id} · ${holder.device_count} devices`}</Text>
            <Text dimColor>{` · ${formatResourceTtl(holder.ttl_seconds)} left`}</Text>
          </Text>
          <Text dimColor>{`    ${holder.intent || 'No intent recorded'}`}</Text>
          {holder.yield_requests.map((request, requestIndex) => (
            <Box key={requestIndex} flexDirection="column">
              <Text color={theme.warning}>{`    yield request · ${request.reason}`}</Text>
              {request.response ? <Text dimColor>{`    ${request.response.decision} · ${request.response.reason}`}</Text> : null}
            </Box>
          ))}
        </Box>
      ))}
      <Text> </Text>
      <Text dimColor>{`queue · ${status.queue.length}`}</Text>
      {status.queue.length === 0 ? <Text dimColor>  (none)</Text> : status.queue.map((request) => (
        <Box key={request.position} flexDirection="column">
          <Text>
            <Text>{`  #${request.position} · ${request.project} · ${request.task_id}`}</Text>
            <Text dimColor>{` · ${formatResourceTtl(request.ttl_seconds)} left`}</Text>
          </Text>
          <Text dimColor>{`    ${request.intent || 'No intent recorded'}`}</Text>
        </Box>
      ))}
    </Box>
  );
}
