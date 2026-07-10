import React from 'react';
import { Box, Text } from 'ink';
import { effortColor } from '../theme.js';
import type { Role } from '../api.js';
import type { ActivityView } from '../../../core/src/activity.js';

const HINTS = 'Enter send · talk naturally · / commands · Ctrl+O activity · scroll up history · Ctrl-C quit';
const COMPACT_HINTS = 'Enter send · / commands · Ctrl+O activity · Ctrl-C quit';

interface Summary {
  backend: string;
  model: string;
  effort: string | null;
  mixed: boolean;
}

/** Collapse the four roles to one backend·model·effort line (like the Python
 *  cockpit): if they all share a config show it once, else show the engineer's
 *  and flag "mixed" (full breakdown is in /roles above). */
export function backendSummary(roles: Role[]): Summary | null {
  if (roles.length === 0) return null;
  const first = roles[0];
  const same = roles.every(
    (r) => r.backend_label === first.backend_label && r.model === first.model && r.effort === first.effort,
  );
  const src = same ? first : roles.find((r) => r.role === 'engineer') ?? first;
  return { backend: src.backend_label, model: src.model, effort: src.effort, mixed: !same };
}

export function Footer({
  notice,
  health,
  roles,
  active,
  width,
}: {
  notice?: string;
  health?: string;
  roles: Role[];
  active?: ActivityView | null;
  width: number;
}) {
  const s = backendSummary(roles);
  const configured = s ? `defaults · ${s.backend} · ${s.model}${s.mixed ? ' · mixed' : ''}` : '';
  const activeBackend = active?.backend
    ? active.backend.charAt(0).toUpperCase() + active.backend.slice(1)
    : s?.backend ?? '';
  const activeSummary = active?.model
    ? `active ${active.role} · ${activeBackend} · ${active.model}`
    : '';
  const backend = activeSummary || configured;
  const rawLeft = notice || (health ? `⚠ ${health}` : '') || (width < 132 ? COMPACT_HINTS : HINTS);
  const leftLimit = width >= 132 && backend ? Math.max(20, width - backend.length - 8) : Math.max(12, width - 2);
  const left = rawLeft.length <= leftLimit ? rawLeft : `${rawLeft.slice(0, leftLimit - 1)}…`;
  if (width < 90) return <Text dimColor wrap="truncate-end">{left}</Text>;
  if (width < 132) {
    return (
      <Box flexDirection="column">
        <Text dimColor wrap="truncate-end">{left}</Text>
        {backend ? <Text dimColor>{backend}</Text> : null}
      </Box>
    );
  }
  return (
    <Box justifyContent="space-between" width="100%">
      <Text dimColor wrap="truncate-end">{left}</Text>
      {activeSummary ? (
        <Text>
          <Text color={effortColor('high')}>{`● ${active?.role ?? 'agent'}`}</Text>
          <Text dimColor>{` · ${activeBackend} · ${active?.model}`}</Text>
        </Text>
      ) : s ? (
        <Text>
          {s.effort ? (
            <>
              <Text color={effortColor(s.effort)}>{`● ${s.effort}`}</Text>
              <Text dimColor>{' · '}</Text>
            </>
          ) : null}
          <Text dimColor>{backend}</Text>
        </Text>
      ) : null}
    </Box>
  );
}
