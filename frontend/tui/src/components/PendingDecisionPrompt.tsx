import React from 'react';
import { Box, Text } from 'ink';
import type { OperatorDecisionCard } from '../../../core/src/decisions.js';
import type { Edit } from '../input/editor.js';
import { theme } from '../theme.js';

export function PendingDecisionPrompt({
  card,
  selection,
  note,
  busy,
  error,
}: {
  card: OperatorDecisionCard;
  selection: number;
  note: Edit;
  busy: boolean;
  error: string;
}) {
  const selected = card.options[selection];
  const freeform = card.options.length === 0;
  const intake = card.kind === 'domain_intake';
  const zh = /[\u3400-\u9fff]/.test(card.title);
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={theme.warning} paddingX={1} marginTop={1}>
      {!intake ? <Text color={theme.warning} bold>ACTION REQUIRED</Text> : null}
      <Text bold wrap="wrap">{card.title}</Text>
      {intake && card.task_title ? <Text dimColor wrap="wrap">{card.task_title}</Text> : null}
      <Box marginTop={1}><Text wrap="wrap">{card.question}</Text></Box>
      {card.options.length ? (
        <Box flexDirection="column" marginTop={1}>
          {card.options.map((option, index) => (
            <Text key={option.id} color={index === selection ? theme.accent : undefined} wrap="wrap">
              {index === selection ? '› ' : '  '}{index + 1}. {option.label}
              {option.description && option.description !== card.question
                ? ` — ${option.description}`
                : ''}
            </Text>
          ))}
        </Box>
      ) : null}
      {intake || freeform || selected?.requires_note ? (
        <Box marginTop={1}>
          <Text color={theme.accent}>{zh ? '回答 / 补充 › ' : 'Your response › '}</Text>
          <Text>{note.value}</Text>
          {!busy ? <Text inverse> </Text> : null}
        </Box>
      ) : null}
      {error ? <Text color={theme.error} wrap="wrap">{error}</Text> : null}
      <Text dimColor>
        {busy
          ? (zh ? '正在提交…' : 'Sending your answer…')
          : intake
            ? (zh ? '↑/↓ 选择 · Enter 确认 · 可输入补充说明' : '↑/↓ select · Enter confirm · type to add details')
          : freeform
            ? 'Type your answer · Enter send'
            : '↑/↓ or number select · Enter confirm · typing selects an option that accepts guidance'}
      </Text>
    </Box>
  );
}
