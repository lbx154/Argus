import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { authoritativeSpend, fraction, type Spend } from '../cost.js';
import type { CostControlSnapshot, Daemon, RequestUsage, UsageSummary } from '../api.js';

/** Live budget gauge backed by the call-level usage ledger. */
export function CostGauge({
  spend,
  settledUsd,
  spendStatus,
  usageSummary,
  daemon,
  requestUsage,
  costControl,
  width,
}: {
  spend: Spend;
  settledUsd?: number | null;
  spendStatus?: string;
  usageSummary?: UsageSummary;
  daemon: Daemon | undefined;
  requestUsage?: RequestUsage | null;
  costControl?: CostControlSnapshot | null;
  width: number;
}) {
  const dailyCap = daemon?.daily_cap_usd ?? null;
  const missionCap = daemon?.per_mission_cap_usd ?? null;
  const total = authoritativeSpend(spend, settledUsd);
  const incomplete = spendStatus === 'partial' || spendStatus === 'unpriced';
  if (
    total <= 0
    && !incomplete
    && !dailyCap
    && !requestUsage
    && !costControl?.reserved_usd
    && !costControl?.unresolved_calls
  ) return null;
  const frac = fraction(spend.last, missionCap);
  const color = frac < 0.6 ? theme.success : frac < 0.85 ? theme.warning : theme.error;
  const codex = requestUsage?.codex;
  const copilot = requestUsage?.copilot;
  return (
    <Box flexDirection="column">
      {(total > 0 || incomplete || dailyCap) ? (
        <Box>
          <Text dimColor>cumulative cost </Text>
          <Text color={color}>
            {settledUsd == null && incomplete
              ? spendStatus
              : `$${total.toFixed(2)}${incomplete ? '+' : ''}`}
          </Text>
          {incomplete && settledUsd != null ? <Text dimColor>{` · ${spendStatus}`}</Text> : null}
          {width >= 80 && spend.missions > 0 ? (
            <Text dimColor>
              {`  · last $${spend.last.toFixed(2)}${incomplete ? '+' : ''}${missionCap ? ` / $${missionCap.toFixed(0)} mission cap` : ''}`}
            </Text>
          ) : null}
          {dailyCap ? <Text dimColor>{width < 80 ? ` · cap $${dailyCap.toFixed(0)}/d` : `  · daily cap $${dailyCap.toFixed(0)}`}</Text> : null}
        </Box>
      ) : null}
      {requestUsage ? (
        <Text dimColor wrap="truncate-end">
          {width < 80
            ? `requests · C ${codex?.daily_calls ?? 0}/${codex?.daily_cap || '∞'} · P ${copilot?.daily_calls ?? 0}/${copilot?.daily_cap || '∞'}`
            : `requests today · Codex ${codex?.daily_calls ?? 0}/${codex?.daily_cap || '∞'} · Copilot ${copilot?.daily_calls ?? 0}/${copilot?.daily_cap || '∞'} · premium ${(copilot?.premium_requests ?? 0).toFixed(1)}/${copilot?.premium_cap || '∞'}`}
        </Text>
      ) : null}
      {costControl && (costControl.reserved_usd > 0 || costControl.unresolved_calls > 0) ? (
        <Text color={costControl.unresolved_calls > 0 ? theme.error : undefined} dimColor={costControl.unresolved_calls === 0}>
          {`cost control · reserved $${costControl.reserved_usd.toFixed(2)} · in-flight ${costControl.active_reservations} · unresolved ${costControl.unresolved_calls}`}
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
