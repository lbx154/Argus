import React from 'react';
import { Box, Text } from 'ink';
import { theme } from '../theme.js';
import type { Role } from '../api.js';

const ORDER = ['manager', 'planner', 'engineer', 'reviewer'];

function fmtAge(age: number | null): string {
  if (age == null) return '';
  if (age < 1) return 'now';
  if (age < 60) return `${Math.floor(age)}s`;
  if (age < 3600) return `${Math.floor(age / 60)}m`;
  return `${Math.floor(age / 3600)}h`;
}

export function RolesBar({ roles, width }: { roles: Role[]; width: number }) {
  const byRole = new Map(roles.map((r) => [r.role, r]));
  if (width < 90) {
    const active = roles.find((role) => role.active);
    return active ? (
      <Box>
        <Text color={theme.role[active.role] ?? 'white'}>● {cap(active.role)}</Text>
        <Text dimColor>{` · ${active.label || active.status || 'active'}`}</Text>
      </Box>
    ) : (
      <Text dimColor>roles · all idle</Text>
    );
  }
  return (
    <Box gap={3}>
      {ORDER.map((name) => {
        const r = byRole.get(name);
        const hue = theme.role[name] ?? 'white';
        const active = !!r?.active;
        const age = r ? fmtAge(r.age_s) : '';
        return (
          <Box key={name}>
            <Text color={active ? hue : 'gray'}>{active ? '●' : '○'}</Text>
            <Text> </Text>
            <Text color={hue} bold={active}>
              {cap(name)}
            </Text>
            <Text> </Text>
            <Text color={active ? undefined : 'gray'} dimColor={!active}>
              {r?.label ?? 'idle'}
            </Text>
            {age ? <Text dimColor>{`  ${age}`}</Text> : null}
          </Box>
        );
      })}
    </Box>
  );
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
