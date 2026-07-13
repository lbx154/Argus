import React, { useMemo } from 'react';
import { Box, Static, Text } from 'ink';
import { toneColor, roleColor, type Rendered } from '../eventRender.js';
import type { EventMsg } from '../api.js';
import { rotate, IDLE_LINES } from '../soul.js';
import {
  buildEventLines,
  partitionEventLines,
  type EventLine,
} from '../eventLines.js';

function EventRow({ r, compact, width }: { r: Rendered; compact: boolean; width: number }) {
  const label = compact ? `${r.label.slice(0, 1)} ` : r.label.padEnd(9);
  const bodyWidth = Math.max(12, width - (compact ? 8 : 15));
  return (
    <Box flexDirection="column">
      {r.rule && <Text dimColor>{'  ──'}</Text>}
      <Box>
        <Text>{'  '}</Text>
        <Text color={roleColor(r.role)} bold>
          {label}
        </Text>
        <Box width={bodyWidth}>
          <Text color={toneColor(r.tone)} wrap={compact || r.tone !== 'bright' ? 'truncate-end' : 'wrap'}>
            {r.glyph} {r.text}
          </Text>
        </Box>
      </Box>
    </Box>
  );
}

/**
 * The event feed — CLEAN (whitelisted, non-noisy: no more ``agent.io.stream``)
 * and coalesced (a streaming message is one growing line, not a flood). Finished
 * lines go through Ink ``<Static>`` so they land in the terminal's OWN
 * scrollback — real, unlimited scroll-up (the Claude Code approach), not a tiny
 * fixed window. Only the currently-streaming line renders live below it.
 */
export function EventLog({
  events,
  width,
  mode = 'all',
  liveMessageId = '',
  collapsed = false,
}: {
  events: EventMsg[];
  width: number;
  mode?: 'all' | 'conversation';
  liveMessageId?: string;
  collapsed?: boolean;
}) {
  const clean = useMemo<EventLine[]>(() => {
    const lines = buildEventLines(events);
    return mode === 'conversation'
      ? lines.filter((line) => ['ui.operator', 'ui.argus'].includes(String(line.ev.type ?? '')))
      : lines;
  }, [events, mode]);

  // A message_id groups fragments but does not imply that a reply is still
  // streaming. The request lifecycle explicitly names the one mutable row.
  const { committed, live } = partitionEventLines(clean, liveMessageId);

  const compact = width < 80;

  return (
    <Box flexDirection="column" marginTop={collapsed ? 0 : 1}>
      <Static items={committed}>{(line) => <EventRow key={line.key} r={line.r} compact={compact} width={width} />}</Static>
      {!collapsed && live ? <EventRow r={live.r} compact={compact} width={width} /> : null}
      {!collapsed && clean.length === 0 ? <Text dimColor>{`  ${rotate(IDLE_LINES)}`}</Text> : null}
    </Box>
  );
}
