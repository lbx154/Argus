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

export type Effort = 'low' | 'medium' | 'high';
export const EFFORTS: Effort[] = ['low', 'medium', 'high'];

/** The settings writes that apply a choice: the project's one model and effort, each only if it changes. */
export function choiceWrites(chosen: { model: string; effort: Effort | '' }, current: { model: string; effort: string }) {
  return [
    ...chosen.model && chosen.model !== current.model ? [{ name: 'ARGUS_SKILL_MODEL', value: chosen.model }] : [],
    ...chosen.effort && chosen.effort !== current.effort ? [{ name: 'ARGUS_SKILL_REASONING_EFFORT', value: chosen.effort }] : [],
  ];
}
