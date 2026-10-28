import type { ModelChoice } from '../api';

export type ModelTier = 'economy' | 'balanced' | 'best';

/**
 * A neutral price band for each priced model, from the price table only (no
 * benchmark): the most expensive output rate is "best", a rate at most a
 * quarter of it is "economy", everything between is "balanced". Unpriced
 * models get no band. With one price there is nothing to compare: balanced.
 */
export function modelTiers(rows: Pick<ModelChoice, 'model' | 'price'>[]): Record<string, ModelTier> {
  const priced = rows.filter(row => row.price && row.price.output > 0);
  const top = Math.max(0, ...priced.map(row => row.price!.output));
  const distinct = new Set(priced.map(row => row.price!.output)).size;
  const tiers: Record<string, ModelTier> = {};
  for (const row of priced) {
    const output = row.price!.output;
    tiers[row.model] = distinct < 2 ? 'balanced'
      : output >= top ? 'best'
        : output <= top / 4 ? 'economy'
          : 'balanced';
  }
  return tiers;
}

/** "$2", "$1.25", "$0.2" — a per-million rate without trailing zeros. */
export function formatRate(usd: number): string {
  return `$${Number(usd.toFixed(2))}`;
}

/** A small amount of money: two decimals, three below ten cents, never a bare "$0.00". */
export function formatUsd(usd: number): string {
  if (usd <= 0) return '$0';
  if (usd < 0.001) return '<$0.001';
  if (usd < 0.1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

/** Seconds below a minute, then minutes with one decimal. */
export function formatLatency(ms: number, locale: 'en' | 'zh-CN'): string {
  const seconds = ms / 1000;
  const zh = locale === 'zh-CN';
  if (seconds < 1) return zh ? '不到 1 秒' : '<1 s';
  if (seconds < 60) return zh ? `${Math.round(seconds)} 秒` : `${Math.round(seconds)} s`;
  const minutes = Number((seconds / 60).toFixed(1));
  return zh ? `${minutes} 分钟` : `${minutes} min`;
}

export const ROLE_MODEL_KNOB = {
  manager: 'ARGUS_SKILL_MANAGER_MODEL',
  planner: 'ARGUS_SKILL_PLAN_MODEL',
  engineer: 'ARGUS_SKILL_ENGINEER_MODEL',
  reviewer: 'ARGUS_SKILL_REVIEWER_MODEL',
} as const;

export const ROLE_EFFORT_KNOB = {
  manager: 'ARGUS_SKILL_MANAGER_REASONING_EFFORT',
  planner: 'ARGUS_SKILL_PLANNER_REASONING_EFFORT',
  engineer: 'ARGUS_SKILL_ENGINEER_REASONING_EFFORT',
  reviewer: 'ARGUS_SKILL_REVIEWER_REASONING_EFFORT',
} as const;

export type ChooserRole = keyof typeof ROLE_MODEL_KNOB;
export const CHOOSER_ROLES = Object.keys(ROLE_MODEL_KNOB) as ChooserRole[];
export type Effort = 'low' | 'medium' | 'high';
export const EFFORTS: Effort[] = ['low', 'medium', 'high'];

/**
 * The settings writes that apply a choice, in order. One model for every role
 * sets the shared model and releases role pins; per role sets only the roles
 * that changed. An effort (when chosen) is written for each role it covers.
 */
export function choiceWrites({ mode, shared, perRole, current, effort, currentEffort }: {
  mode: 'all' | 'roles';
  shared: string;
  perRole: Record<ChooserRole, string>;
  current: Record<ChooserRole, string>;
  effort: Effort | '';
  currentEffort: Record<ChooserRole, string>;
}): Array<{ name: string; value: string; applyToRoles?: boolean }> {
  const writes: Array<{ name: string; value: string; applyToRoles?: boolean }> = [];
  if (mode === 'all') {
    if (shared && CHOOSER_ROLES.some(role => current[role] !== shared)) {
      writes.push({ name: 'ARGUS_SKILL_MODEL', value: shared, applyToRoles: true });
    }
  } else {
    for (const role of CHOOSER_ROLES) {
      if (perRole[role] && perRole[role] !== current[role]) writes.push({ name: ROLE_MODEL_KNOB[role], value: perRole[role] });
    }
  }
  if (effort) {
    for (const role of CHOOSER_ROLES) {
      if (currentEffort[role] !== effort) writes.push({ name: ROLE_EFFORT_KNOB[role], value: effort });
    }
  }
  return writes;
}
