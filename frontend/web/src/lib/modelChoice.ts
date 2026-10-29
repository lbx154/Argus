/** "$2", "$1.25", "$0.2" — a per-million rate without trailing zeros. */
export function formatRate(usd: number): string {
  return `$${Number(usd.toFixed(2))}`;
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

export const automaticModel = (model: string | null | undefined): boolean =>
  !model || ['auto', 'inherit', 'default'].includes(model.toLowerCase());

/** Automatic names the backend's reported default, independently of past calls. */
export function modelChoiceLabel(model: string | null | undefined, defaultModel: string | undefined, locale: 'en' | 'zh-CN'): string {
  if (!automaticModel(model)) return model!;
  const automatic = locale === 'zh-CN' ? '自动' : 'Automatic';
  return defaultModel ? (locale === 'zh-CN' ? `${automatic}（${defaultModel}）` : `${automatic} (${defaultModel})`) : automatic;
}

/** The settings writes that apply a choice: the project's one model ('' for the backend's default)
 * and effort, each only if it changes. */
export function choiceWrites(chosen: { model: string; effort: Effort | '' }, current: { model: string; effort: string }) {
  return [
    ...chosen.model !== current.model ? [{ name: 'ARGUS_SKILL_MODEL', value: chosen.model }] : [],
    ...chosen.effort && chosen.effort !== current.effort ? [{ name: 'ARGUS_SKILL_REASONING_EFFORT', value: chosen.effort }] : [],
  ];
}
