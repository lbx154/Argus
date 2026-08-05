import React, { useEffect, useMemo, useRef } from 'react';
import { Box, Static, Text, useStdout } from 'ink';
import { toneColor, roleColor, type Rendered } from '../eventRender.js';
import type { EventMsg } from '../api.js';
import { rotate, IDLE_LINES } from '../soul.js';
import {
  buildEventLines,
  partitionEventLines,
  type EventLine,
} from '../eventLines.js';
import { fragmentMode } from '../../../core/src/events.js';

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
          <Text
            color={r.reasoning ? undefined : toneColor(r.tone)}
            dimColor={r.reasoning}
            italic={r.reasoning}
            wrap={r.expand || r.tone === 'bright' ? 'wrap' : 'truncate-end'}
          >
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
  showIdle = true,
  // Hidden unless asked for, so a caller that forgets the prop cannot leak the
  // scratchpad. App.tsx passes the resolved knob explicitly.
  showReasoning = false,
}: {
  events: EventMsg[];
  width: number;
  mode?: 'all' | 'conversation';
  liveMessageId?: string;
  collapsed?: boolean;
  showIdle?: boolean;
  showReasoning?: boolean;
}) {
  const clean = useMemo<EventLine[]>(() => {
    const lines = buildEventLines(events);
    const visible = showReasoning
      ? lines
      : lines.filter((line) => !line.r.reasoning);
    return mode === 'conversation'
      ? visible.filter((line) => (
          ['ui.operator', 'ui.argus', 'ui.activity'].includes(String(line.ev.type ?? ''))
          || line.r.reasoning
        ))
      : visible;
  }, [events, mode, showReasoning]);

  // A message_id groups fragments but does not imply that a reply is still
  // streaming. The request lifecycle explicitly names the one mutable row.
  const { committed, live } = partitionEventLines(clean, liveMessageId);
  const streamableLive = live && fragmentMode(live.ev) === 'append'
    ? live
    : null;
  const reactLive = live && !streamableLive && live.ev.type !== 'ui.argus'
    ? live
    : null;
  const { write } = useStdout();
  const streamedKeys = useRef(new Set<string>());
  const activeStream = useRef<{ key: string; text: string } | null>(null);

  const compact = width < 80;
  const hideEmptyBody = !showIdle && clean.length === 0;
  const staticItems = committed.filter((line) => (
    !streamedKeys.current.has(line.key)
    || fragmentMode(line.ev) !== 'append'
  ));

  useEffect(() => {
    if (!streamableLive) {
      if (activeStream.current) {
        write('\n');
        activeStream.current = null;
      }
      return;
    }

    const previous = activeStream.current;
    let chunk = '';
    if (!previous || previous.key !== streamableLive.key) {
      if (previous) write('\n');
      const label = compact
        ? `${streamableLive.r.label.slice(0, 1)} `
        : streamableLive.r.label.padEnd(9);
      chunk = `  ${label}${streamableLive.r.glyph} ${streamableLive.r.text}`;
    } else if (streamableLive.r.text.startsWith(previous.text)) {
      chunk = streamableLive.r.text.slice(previous.text.length);
    } else if (streamableLive.r.text !== previous.text) {
      const label = compact
        ? `${streamableLive.r.label.slice(0, 1)} `
        : streamableLive.r.label.padEnd(9);
      chunk = `\n  ${label}${streamableLive.r.glyph} ${streamableLive.r.text}`;
    }

    streamedKeys.current.add(streamableLive.key);
    activeStream.current = {
      key: streamableLive.key,
      text: streamableLive.r.text,
    };
    if (chunk) write(chunk);
  }, [
    compact,
    live?.key,
    streamableLive?.key,
    streamableLive?.r.glyph,
    streamableLive?.r.label,
    streamableLive?.r.text,
    write,
  ]);

  return (
    <Box flexDirection="column" marginTop={collapsed || hideEmptyBody ? 0 : 1}>
      <Static items={staticItems}>{(line) => <EventRow key={line.key} r={line.r} compact={compact} width={width} />}</Static>
      {!collapsed && reactLive ? (
        <EventRow r={reactLive.r} compact={compact} width={width} />
      ) : null}
      {!collapsed && showIdle && clean.length === 0 ? <Text dimColor>{`  ${rotate(IDLE_LINES)}`}</Text> : null}
    </Box>
  );
}
