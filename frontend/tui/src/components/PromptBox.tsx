import React from 'react';
import { Box, Text } from 'ink';
import stringWidth from 'string-width';
import { theme } from '../theme.js';
import type { Edit } from '../input/editor.js';

const MAX_INPUT_ROWS = 4;
const FULL_LABEL = 'talk to Argus › ';
const COMPACT_LABEL = '› ';

type SegmentKind = 'text' | 'dim' | 'caret';

interface Segment {
  kind: SegmentKind;
  text: string;
  width: number;
}

function displayChar(char: string): string {
  if (char === '\n') return '↵';
  if (char === '\t') return '⇥';
  return char;
}

function appendSegment(row: Segment[], segment: Segment): void {
  const previous = row[row.length - 1];
  if (previous && previous.kind === segment.kind && segment.kind !== 'caret') {
    previous.text += segment.text;
    previous.width += segment.width;
    return;
  }
  row.push({ ...segment });
}

function wrapSegments(segments: Segment[], columns: number): Segment[][] {
  const rows: Segment[][] = [];
  let row: Segment[] = [];
  let used = 0;
  for (const segment of segments) {
    if (row.length > 0 && segment.width > 0 && used + segment.width > columns) {
      rows.push(row);
      row = [];
      used = 0;
    }
    appendSegment(row, segment);
    used += segment.width;
  }
  if (row.length > 0) rows.push(row);
  return rows;
}

function cursorWindow(edit: Edit, capacity: number): {
  chars: string[];
  cursor: number;
  start: number;
  end: number;
} {
  const chars = Array.from(edit.value);
  const cursor = Math.max(0, Math.min(edit.cursor, chars.length));
  const widths = chars.map((char) => stringWidth(displayChar(char)));
  const virtualCaretWidth = cursor === chars.length ? 1 : 0;
  const totalWidth = widths.reduce((sum, width) => sum + width, 0) + virtualCaretWidth;
  if (totalWidth <= capacity) return { chars, cursor, start: 0, end: chars.length };

  // Reserve room for clipping marks and the caret, then keep more context
  // before the caret than after it while still filling any spare capacity.
  const sourceBudget = Math.max(1, capacity - 2 - virtualCaretWidth);
  let start = cursor;
  let end = cursor;
  let used = 0;
  if (cursor < chars.length) {
    end = cursor + 1;
    used = widths[cursor];
  }

  const beforeTarget = Math.max(0, Math.floor((sourceBudget - used) * 0.7));
  let beforeUsed = 0;
  while (start > 0 && beforeUsed + widths[start - 1] <= beforeTarget) {
    start -= 1;
    beforeUsed += widths[start];
    used += widths[start];
  }
  while (end < chars.length && used + widths[end] <= sourceBudget) {
    used += widths[end];
    end += 1;
  }
  while (start > 0 && used + widths[start - 1] <= sourceBudget) {
    start -= 1;
    used += widths[start];
  }
  return { chars, cursor, start, end };
}

function promptRows(edit: Edit, columns: number): {
  rows: Segment[][];
  clipped: boolean;
  length: number;
} {
  // Wide glyphs can leave one unusable cell at a line boundary. Reserve that
  // possible slack so CJK and emoji still fit within the row limit.
  const capacity = columns * MAX_INPUT_ROWS - (MAX_INPUT_ROWS - 1);
  const { chars, cursor, start, end } = cursorWindow(edit, capacity);
  const segments: Segment[] = [];
  if (start > 0) segments.push({ kind: 'dim', text: '…', width: 1 });
  for (let index = start; index < end; index += 1) {
    const text = displayChar(chars[index]);
    segments.push({
      kind: index === cursor ? 'caret' : 'text',
      text,
      width: stringWidth(text),
    });
  }
  if (cursor === chars.length) segments.push({ kind: 'caret', text: '▏', width: 1 });
  if (end < chars.length) segments.push({ kind: 'dim', text: '…', width: 1 });
  return {
    rows: wrapSegments(segments, columns),
    clipped: start > 0 || end < chars.length,
    length: chars.length,
  };
}

/** Rounded, wrapped input that keeps the caret visible without taking over the terminal. */
export function PromptBox({ edit, width = 80 }: { edit: Edit; width?: number }) {
  const label = width < 52 ? COMPACT_LABEL : FULL_LABEL;
  // App has two columns of outer padding; the box border and padding use four
  // more. Keeping that reserve here makes manual wrapping stable in real use.
  const contentColumns = Math.max(4, width - 6 - stringWidth(`◆ ${label}`));
  const view = promptRows(edit, contentColumns);
  return (
    <Box
      borderStyle="round"
      borderColor={theme.border}
      paddingX={1}
      marginTop={1}
      width="100%"
      minHeight={3}
      flexShrink={0}
      overflow="hidden"
      alignItems="flex-start"
    >
      <Box flexShrink={0}>
        <Text color={theme.accent}>◆ </Text>
        <Text dimColor>{label}</Text>
      </Box>
      <Box flexDirection="column" width={contentColumns}>
        {view.rows.map((row, rowIndex) => (
          <Text key={rowIndex} wrap="truncate-end">
            {row.map((segment, segmentIndex) => (
              segment.kind === 'caret'
                ? <Text key={segmentIndex} inverse>{segment.text}</Text>
                : <Text key={segmentIndex} dimColor={segment.kind === 'dim'}>{segment.text}</Text>
            ))}
          </Text>
        ))}
        {view.clipped ? (
          <Box justifyContent="flex-end">
            <Text dimColor wrap="truncate-end">{`${view.length} chars`}</Text>
          </Box>
        ) : null}
      </Box>
    </Box>
  );
}
