import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { fraction } from '../cost.js';
import type { CostControlSnapshot, Daemon, RequestUsage, UsageSummary } from '../api.js';

/** Model/API-call spend only; GPU and infrastructure cost are out of scope. */
export function CostGauge({
  settledUsd,
  spendStatus,
  usageSummary,
  daemon,
  requestUsage,
  costControl,
  width,
}: {
  settledUsd?: number | null;
  spendStatus?: string;
  usageSummary?: UsageSummary;
  daemon: Daemon | undefined;
  requestUsage?: RequestUsage | null;
  costControl?: CostControlSnapshot | null;
  width: number;
}) {
  const globalCap = daemon?.global_daily_cap_usd ?? null;
  const total = settledUsd ?? 0;
  const incomplete = spendStatus === 'partial' || spendStatus === 'unpriced';
  if (
    total <= 0
    && !incomplete
    && !globalCap
    && !requestUsage
    && !costControl?.active_reservations
    && !costControl?.unresolved_calls
  ) return null;
  const frac = fraction(total, globalCap);
  const color = frac < 0.6 ? theme.success : frac < 0.85 ? theme.warning : theme.error;
  const codex = requestUsage?.codex;
  const copilot = requestUsage?.copilot;
  return (
    <Box flexDirection="column">
      {(total > 0 || incomplete || globalCap) ? (
        <Box>
          <Text dimColor>model/API spend </Text>
          <Text color={color}>
            {settledUsd == null && incomplete
              ? spendStatus
              : `$${total.toFixed(2)}${incomplete ? '+' : ''}`}
          </Text>
          {incomplete && settledUsd != null ? <Text dimColor>{` · ${spendStatus}`}</Text> : null}
          {globalCap ? <Text dimColor>{` · model cap $${globalCap.toFixed(0)}/d`}</Text> : null}
        </Box>
      ) : null}
      {requestUsage ? (
        <Text dimColor wrap="truncate-end">
          {width < 80
            ? `requests · C ${codex?.daily_calls ?? 0}/${codex?.daily_cap || '∞'} · P ${copilot?.daily_calls ?? 0}/${copilot?.daily_cap || '∞'}`
            : `requests today · Codex ${codex?.daily_calls ?? 0}/${codex?.daily_cap || '∞'} · Copilot ${copilot?.daily_calls ?? 0}/${copilot?.daily_cap || '∞'} · premium ${(copilot?.premium_requests ?? 0).toFixed(1)}/${copilot?.premium_cap || '∞'}`}
        </Text>
      ) : null}
      {costControl && (costControl.active_reservations > 0 || costControl.unresolved_calls > 0) ? (
        <Text color={(costControl.blocking_unresolved_calls ?? 0) > 0 ? theme.error : undefined} dimColor={(costControl.blocking_unresolved_calls ?? 0) === 0}>
          {`cost control · in-flight ${costControl.active_reservations} · unresolved ${costControl.unresolved_calls}`}
        </Text>
      ) : null}
      {usageSummary && usageSummary.call_count > 0 ? (
        <Text dimColor wrap="truncate-end">
          {width < 80
            ? `tokens · in ${usageSummary.input_tokens} · out ${usageSummary.output_tokens}`
            : `tokens · input ${usageSummary.input_tokens} · cache read ${usageSummary.cached_input_tokens} · cache write ${usageSummary.cache_write_tokens} · output ${usageSummary.output_tokens} · reasoning ${usageSummary.reasoning_output_tokens}`}
        </Text>
      ) : null}
    </Box>
  );
}
