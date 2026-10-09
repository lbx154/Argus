import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { UsageSummary } from '../api.js';

function incomplete(summary?: UsageSummary | null): boolean {
  return summary?.pricing_status === 'partial' || summary?.pricing_status === 'unpriced';
}

/** A ledger total as text: the settled amount, with "+" while some calls are still unpriced. */
function spendText(summary?: UsageSummary | null): string {
  if (summary?.cost_usd == null) return summary && incomplete(summary) ? summary.pricing_status : '$0.00';
  return `$${summary.cost_usd.toFixed(2)}${incomplete(summary) ? '+' : ''}`;
}

/** Model/API-call spend as recorded in the usage ledgers; GPU and infrastructure cost are out of scope. Nothing is capped here. */
export function CostGauge({
  usage,
  globalUsage,
  width,
}: {
  /** This project's ledger. */
  usage?: UsageSummary | null;
  /** Today's ledger across all projects. */
  globalUsage?: UsageSummary | null;
  width: number;
}) {
  return (
    <Box flexDirection="column">
      <Box>
        <Text dimColor>model/API spend </Text>
        <Text color={incomplete(usage) ? theme.warning : theme.success}>{spendText(usage)}</Text>
        <Text dimColor>{` · today, all projects ${spendText(globalUsage)}`}</Text>
      </Box>
      {usage && usage.call_count > 0 ? (
        <Text dimColor wrap="truncate-end">
          {width < 80
            ? `tokens · in ${usage.input_tokens} · out ${usage.output_tokens}`
            : `tokens · input ${usage.input_tokens} · cache read ${usage.cached_input_tokens} · cache write ${usage.cache_write_tokens} · output ${usage.output_tokens} · reasoning ${usage.reasoning_output_tokens}`}
          {usage.premium_requests > 0 ? ` · premium ${usage.premium_requests.toFixed(1)}` : ''}
        </Text>
      ) : null}
    </Box>
  );
}
