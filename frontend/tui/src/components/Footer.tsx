import React from 'react';
import { Text } from 'ink';

const HINTS = 'Enter send · / commands · Ctrl+O operations · Ctrl+T reasoning · scroll up · Ctrl-C quit';
const COMPACT_HINTS = 'Enter send · / commands · Ctrl+O operations · Ctrl+T reasoning · Ctrl-C quit';

export function Footer({
  notice,
  health,
  width,
}: {
  notice?: string;
  health?: string;
  width: number;
}) {
  const raw = notice || (health ? `⚠ ${health}` : '') || (width < 132 ? COMPACT_HINTS : HINTS);
  const limit = Math.max(12, width - 2);
  const text = raw.length <= limit ? raw : `${raw.slice(0, limit - 1)}…`;
  return <Text dimColor wrap="truncate-end">{text}</Text>;
}
