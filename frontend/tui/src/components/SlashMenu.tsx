import React, { useRef } from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { SlashCmd } from '../input/slash.js';

const MAX_POPUP_ROWS = 8;
const MIN_COMPOSER_ROWS = 3;
const RESERVED_CHROME_ROWS = 10;

export interface SlashMenuWindow {
  start: number;
  end: number;
  selected: number;
}

/** Scale the popup to the viewport while preserving the composer and surrounding chrome. */
export function slashMenuVisibleRows(viewportRows: number): number {
  const rows = Number.isFinite(viewportRows) ? Math.max(1, Math.floor(viewportRows)) : 24;
  return Math.max(
    1,
    Math.min(MAX_POPUP_ROWS, rows - MIN_COMPOSER_ROWS - RESERVED_CHROME_ROWS),
  );
}

/** Keep the selected command inside a bounded scrolling window. */
export function slashMenuWindow(
  itemCount: number,
  selected: number,
  maxVisible: number,
  scrollTop = 0,
): SlashMenuWindow {
  if (itemCount <= 0) return { start: 0, end: 0, selected: -1 };
  const requested = Number.isFinite(maxVisible) ? Math.floor(maxVisible) : MAX_POPUP_ROWS;
  const visible = Math.max(1, Math.min(itemCount, requested));
  const safeSelected = Math.max(0, Math.min(itemCount - 1, selected));
  let start = Math.max(0, Math.min(itemCount - visible, scrollTop));
  if (safeSelected < start) start = safeSelected;
  if (safeSelected >= start + visible) start = safeSelected + 1 - visible;
  return { start, end: start + visible, selected: safeSelected };
}

/** Slash-command completion dropdown, shown while typing a `/command` token. */
export function SlashMenu({
  items,
  selected,
  maxVisible = MAX_POPUP_ROWS,
}: {
  items: SlashCmd[];
  selected: number;
  maxVisible?: number;
}) {
  const scrollTop = useRef(0);
  const view = slashMenuWindow(items.length, selected, maxVisible, scrollTop.current);
  scrollTop.current = view.start;

  if (items.length === 0) {
    scrollTop.current = 0;
    return null;
  }
  return (
    <Box flexDirection="column" marginTop={1} marginLeft={1} overflow="hidden">
      {items.slice(view.start, view.end).map((c, i) => {
        const absoluteIndex = view.start + i;
        const on = absoluteIndex === view.selected;
        return (
          <Box key={c.name} height={1} width="100%" overflow="hidden">
            <Text wrap="truncate-end">
              <Text color={on ? theme.accent : undefined} bold={on}>
                {on ? '❯ ' : '  '}
                {c.name}
                {c.arg ? ` ${c.arg}` : ''}
              </Text>
              <Text dimColor>{`   ${c.desc}`}</Text>
            </Text>
          </Box>
        );
      })}
      <Box height={1} overflow="hidden">
        <Text dimColor wrap="truncate-end">
          {`  ↑↓ ${view.selected + 1}/${items.length} · Tab complete · Esc dismiss`}
        </Text>
      </Box>
    </Box>
  );
}
