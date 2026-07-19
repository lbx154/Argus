import React from 'react';
import { Box, Text } from 'ink';

import {
  formatMissionElapsed,
  metricDisplay,
  missionMetricImprovement,
} from '../../../core/src/missionView.js';
import type { MissionTimelineItem, MissionView } from '../../../core/src/types.js';
import type { RequestUsage } from '../api.js';
import { theme } from '../theme.js';

const ROLE_ORDER = ['manager', 'planner', 'engineer', 'reviewer'];

function cap(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function compact(text: string, width: number): string {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  return value.length <= width ? value : `${value.slice(0, Math.max(1, width - 1))}…`;
}

function timelineColor(item: MissionTimelineItem): string | undefined {
  if (item.tone === 'error') return theme.error;
  if (item.tone === 'success' || item.tone === 'metric' || item.tone === 'skill') return theme.success;
  if (item.tone === 'info') return theme.info;
  return undefined;
}

export function budgetSummary(
  spentUsd?: number | null,
  spendStatus?: string,
  dailyCapUsd?: number | null,
  globalDailyCapUsd?: number | null,
  wide = true,
): string {
  const spent = spentUsd == null
    ? spendStatus && spendStatus !== 'empty' ? spendStatus : '$0.00 spent'
    : `$${spentUsd.toFixed(2)} spent`;
  const daily = dailyCapUsd ? ` / $${dailyCapUsd.toFixed(0)} daily` : '';
  const global = globalDailyCapUsd && wide ? ` · $${globalDailyCapUsd.toFixed(0)} global` : '';
  return spent + daily + global;
}

export function requestSummary(requestUsage?: RequestUsage | null): string {
  const codex = requestUsage?.codex;
  const copilot = requestUsage?.copilot;
  return [
    `Codex ${codex?.daily_calls ?? 0}/${codex?.daily_cap || '∞'}`,
    `Copilot ${copilot?.daily_calls ?? 0}/${copilot?.daily_cap || '∞'}`,
    `premium ${(copilot?.premium_requests ?? 0).toFixed(1)}/${copilot?.premium_cap || '∞'}`,
  ].join(' · ');
}

export function MissionCockpit({
  view,
  width,
  height,
  spentUsd,
  spendStatus,
  dailyCapUsd,
  globalDailyCapUsd,
  requestUsage,
}: {
  view: MissionView;
  width: number;
  height?: number;
  spentUsd?: number | null;
  spendStatus?: string;
  dailyCapUsd?: number | null;
  globalDailyCapUsd?: number | null;
  requestUsage?: RequestUsage | null;
}) {
  const mission = view.mission.objective || view.mission.title || 'Waiting for a mission';
  const metric = view.primary_metric;
  const improvement = missionMetricImprovement(metric);
  const timeline = view.timeline.slice(-Math.max(3, width < 80 ? 4 : 6));
  const roleByName = new Map(view.roles.map((role) => [role.role, role]));
  const recentRoles = view.timeline
    .map((item) => item.role)
    .filter((role, index, rows) => ROLE_ORDER.includes(role) && (index === 0 || role !== rows[index - 1]));
  const handoff = recentRoles.length > 1
    ? `${cap(recentRoles[recentRoles.length - 2])} → ${cap(recentRoles[recentRoles.length - 1])}`
    : '';
  const stage = view.stage.label || view.stage.id || '—';
  const round = view.round.max > 0 ? `${view.round.current} / ${view.round.max}` : view.round.current ? String(view.round.current) : '—';
  const compactHeight = height != null && height < 26;

  if (compactHeight) {
    const latest = timeline[timeline.length - 1];
    return (
      <Box flexDirection="column" marginTop={1}>
        <Text dimColor>MISSION</Text>
        <Text bold>{compact(mission, Math.max(24, width - 2))}</Text>
        <Text wrap="truncate-end">
          <Text dimColor>STAGE </Text>
          <Text color={theme.info}>{compact(stage, 20)}</Text>
          <Text dimColor>{` · ROUND ${round} · ELAPSED `}</Text>
          <Text>{formatMissionElapsed(view.mission.elapsed_seconds)}</Text>
          <Text dimColor> · BEST </Text>
          <Text color={metric?.verification_status === 'accepted' ? theme.success : theme.warning}>
            {metricDisplay(metric)}
          </Text>
        </Text>
        <Text wrap="truncate-end">
          <Text dimColor>BUDGET </Text>
          <Text color={spendStatus === 'partial' || spendStatus === 'unpriced' ? theme.warning : theme.success}>
            {budgetSummary(spentUsd, spendStatus, dailyCapUsd, globalDailyCapUsd, false)}
          </Text>
          <Text dimColor>{` · ${requestSummary(requestUsage)}`}</Text>
        </Text>
        <Box flexDirection="column">
          <Text dimColor>AI RESEARCH TEAM</Text>
          {ROLE_ORDER.map((name) => {
            const role = roleByName.get(name);
            const status = role?.status ?? 'waiting';
            const glyph = status === 'active'
              ? '●'
              : status === 'done'
              ? '✓'
              : status === 'rejected' || status === 'error'
              ? '!'
              : '○';
            const color = status === 'rejected' || status === 'error'
              ? theme.error
              : status === 'done'
              ? theme.success
              : status === 'active'
              ? theme.role[name] ?? theme.info
              : 'gray';
            return (
              <Box key={name}>
                <Box width={11}>
                  <Text color={theme.role[name] ?? 'white'} bold>{name.toUpperCase()}</Text>
                </Box>
                <Text color={color}>{glyph} </Text>
                <Text color={status === 'waiting' ? 'gray' : undefined} dimColor={status === 'waiting'}>
                  {compact(role?.label || (status === 'waiting' ? 'Waiting' : cap(status)), Math.max(20, width - 16))}
                </Text>
              </Box>
            );
          })}
        </Box>
        <Text wrap="truncate-end">
          <Text dimColor>TIMELINE </Text>
          {latest ? (
            <Text color={timelineColor(latest)}>
              {compact(latest.title + (latest.detail ? ` · ${latest.detail}` : ''), Math.max(18, width - 12))}
            </Text>
          ) : <Text dimColor>Waiting for structured research events…</Text>}
        </Text>
      </Box>
    );
  }

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text dimColor>MISSION</Text>
      <Text bold>{compact(mission, Math.max(24, width - 2))}</Text>

      <Box marginTop={1} gap={width >= 76 ? 4 : 2}>
        <Text dimColor>STAGE </Text>
        <Text color={theme.info}>{compact(stage, width < 76 ? 14 : 22)}</Text>
        <Text dimColor> ELAPSED </Text>
        <Text>{formatMissionElapsed(view.mission.elapsed_seconds)}</Text>
      </Box>
      <Box gap={width >= 76 ? 4 : 2}>
        <Text dimColor>ROUND </Text>
        <Text>{round}</Text>
        <Text dimColor> BEST </Text>
        <Text color={metric?.verification_status === 'accepted' ? theme.success : theme.warning}>
          {metricDisplay(metric)}
          {improvement != null ? `  ${improvement >= 0 ? '↑' : '↓'}${Math.abs(improvement).toFixed(1)}${metric?.unit || ''}` : ''}
          {metric && metric.verification_status !== 'accepted' ? ' · reported' : ''}
        </Text>
      </Box>
      <Box>
        <Text dimColor>BUDGET </Text>
        <Text color={spendStatus === 'partial' || spendStatus === 'unpriced' ? theme.warning : theme.success}>
          {budgetSummary(
            spentUsd,
            spendStatus,
            dailyCapUsd,
            globalDailyCapUsd,
            width >= 90,
          )}
        </Text>
      </Box>
      <Box>
        <Text dimColor>REQUESTS </Text>
        <Text dimColor wrap="truncate-end">{requestSummary(requestUsage)}</Text>
      </Box>

      <Box flexDirection="column" marginTop={1}>
        <Text dimColor>AI RESEARCH TEAM</Text>
        {handoff ? <Text dimColor>{`handoff · ${handoff}`}</Text> : null}
        {ROLE_ORDER.map((name) => {
          const role = roleByName.get(name);
          const status = role?.status ?? 'waiting';
          const glyph = status === 'active'
            ? '●'
            : status === 'done'
            ? '✓'
            : status === 'rejected' || status === 'error'
            ? '!'
            : '○';
          const color = status === 'rejected' || status === 'error'
            ? theme.error
            : status === 'done'
            ? theme.success
            : status === 'active'
            ? theme.role[name] ?? theme.info
            : 'gray';
          return (
            <Box key={name}>
              <Box width={11}><Text color={theme.role[name] ?? 'white'} bold>{name.toUpperCase()}</Text></Box>
              <Text color={color}>{glyph} </Text>
              <Text color={status === 'waiting' ? 'gray' : undefined} dimColor={status === 'waiting'}>
                {compact(role?.label || (status === 'waiting' ? 'Waiting' : cap(status)), Math.max(20, width - 16))}
              </Text>
            </Box>
          );
        })}
      </Box>

      <Box flexDirection="column" marginTop={1}>
        <Text dimColor>LIVE RESEARCH TIMELINE</Text>
        {timeline.length ? timeline.map((item) => (
          <Box key={item.id}>
            <Text dimColor>{`${new Date(item.ts * 1000).toISOString().slice(11, 16)} `}</Text>
            <Text color={timelineColor(item)}>{item.tone === 'error' ? '!' : item.tone === 'success' || item.tone === 'metric' || item.tone === 'skill' ? '✓' : '·'} </Text>
            <Text color={timelineColor(item)}>{compact(item.title + (item.detail ? ` · ${item.detail}` : ''), Math.max(18, width - 10))}</Text>
          </Box>
        )) : <Text dimColor>  Waiting for structured research events…</Text>}
      </Box>
    </Box>
  );
}
