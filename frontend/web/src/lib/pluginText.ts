import { useI18n } from '../i18n';
import english from './pluginEnglish.json';
const words: Record<string, string> = english;
const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const token = /⟦\d+⟧/g;
const patterns = Object.entries(words).filter(([source]) => source.includes('⟦')).sort((a, b) => b[0].length - a[0].length).map(([source, target]) => ({
  regex: new RegExp('^' + source.split(token).map(escape).join('([\\s\\S]*?)') + '$'), slots: source.match(token) ?? [], target,
}));
const fragments = new RegExp(Object.keys(words).filter(key => key.length > 1 && !key.includes('⟦')).sort((a, b) => b.length - a.length).map(escape).join('|'), 'g');
export function pluginText(value: string, locale: string): string {
  if (locale === 'zh-CN' || !/[\u3400-\u9fff]/.test(value)) return value;
  if (words[value.trim()]) return value.replace(value.trim(), words[value.trim()]);
  for (const pattern of patterns) {
    const match = pattern.regex.exec(value.trim());
    if (match) {
      const substitutions = new Map(pattern.slots.map((slot, i) => [slot, match[i + 1]]));
      return pattern.target.replace(token, slot => substitutions.get(slot) ?? slot);
    }
  }
  return value.replace(fragments, key => words[key]);
}
export function usePluginText() {
  const { locale } = useI18n();
  return <T,>(value: T): T => typeof value === 'string' ? pluginText(value, locale) as T : value;
}
