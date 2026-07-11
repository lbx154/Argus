import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import { authoritativeSpend, fraction, type Spend } from '../cost.js';
import type { Daemon, RequestUsage } from '../api.js';

/** Live budget gauge backed by the call-level usage ledger. */
export function CostGauge({
  spend,
  settledUsd,
  spendStatus,
  daemon,
  requestUsage,
  width,
}: {
  spend: Spend;
  settledUsd?: number | null;
  spendStatus?: string;
  daemon: Daemon | undefined;
  requestUsage?: RequestUsage;
  width: number;
}) {
  const dailyCap = daemon?.daily_cap_usd ?? null;
  const missionCap = daemon?.per_mission_cap_usd ?? null;
  const total = authoritativeSpend(spend, settledUsd);
  const incomplete = spendStatus === 'partial' || spendStatus === 'unpriced';
  if (total <= 0 && !incomplete && !dailyCap && !requestUsage) return null;
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
    </Box>
  );
}
