import React, { useEffect, useState } from 'react';
import { Box, Text } from 'ink';
import type { EventMsg } from '../api.js';
import { latestRunningActivity } from '../../../core/src/activity.js';
import { SPINNER, theme } from '../theme.js';

const cap = (value: string): string => value.charAt(0).toUpperCase() + value.slice(1);

function elapsed(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${minutes}m${rest ? ` ${rest}s` : ''}`;
}

/** One truthful, mutable progress line. It never appends timer-driven chatter. */
export function LiveActivity({
  events,
  width,
  excludeRoles = [],
  background = false,
}: {
  events: EventMsg[];
  width: number;
  excludeRoles?: string[];
  background?: boolean;
}) {
  const activity = latestRunningActivity(events, excludeRoles);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!activity) return;
    const id = setInterval(() => setTick((value) => value + 1), 120);
    return () => clearInterval(id);
  }, [activity?.id]);
  if (!activity) return null;

  const now = Date.now() / 1000;
  const age = Math.max(0, now - activity.startedTs);
  const meta = [activity.model, activity.detail].filter(Boolean).join(' · ');
  return (
    <Box marginTop={1}>
      <Text color={theme.role[activity.role] ?? theme.info}>
        {`${SPINNER[tick % SPINNER.length]} ${background ? 'Background · ' : ''}${cap(activity.role)} `}
      </Text>
      <Text>{activity.label}</Text>
      <Text dimColor>{` · ${elapsed(age)}`}</Text>
      {width >= 110 && meta ? <Text dimColor>{` · ${meta}`}</Text> : null}
      <Text dimColor>{' · Ctrl+O details'}</Text>
    </Box>
  );
}
