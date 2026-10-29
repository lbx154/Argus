import type { SkillLibraryItem } from '../api';
import chinese from './bundledSkillChinese.json';

export interface SkillGuide { title: string; summary: string; originalName: string; source: string; sourceBytes: number }
const guides: Record<string, SkillGuide> = chinese;

/** Display aliases only. Never relabel a learned/edited/third-party document as
 * our bundled instructions merely because its filename happens to match. */
export function bundledSkillGuide(item: SkillLibraryItem): SkillGuide | null {
  if (!item.is_default || !['bundled', 'shared'].includes(item.source)) return null;
  const libraries = item.scope === 'global' ? [`global:${item.source}`]
    : item.scope === 'vertical' ? [`vertical:${item.vertical}:${item.source}`, ...(item.source === 'bundled' ? [`domain:${item.vertical}:bundled`] : [])] : [];
  if (!libraries.includes(item.library)) return null;
  const key = item.scope === 'global' ? `global/${item.path}` : `vertical/${item.vertical}/${item.path}`;
  const guide = Object.hasOwn(guides, key) ? guides[key] : null;
  return guide?.originalName === item.name ? guide : null;
}
export function skillPresentation(item: SkillLibraryItem, locale: string) {
  const guide = locale === 'zh-CN' ? bundledSkillGuide(item) : null;
  return { title: guide?.title ?? item.name,
    summary: guide?.summary ?? (locale === 'zh-CN' && !/[\u3400-\u9fff]/.test(item.description) ? '' : item.description), guide };
}
export function skillSearchText(item: SkillLibraryItem, locale: string): string {
  const shown = skillPresentation(item, locale);
  return `${item.name} ${item.description} ${item.path} ${item.vertical} ${shown.title} ${shown.summary}`.toLocaleLowerCase();
}
const verticals: Record<string, string> = {
  research: '科研', software: '软件开发', math: '数学研究', chemistry: '化学',
  kernel_engineering: 'GPU 算子工程', argus_maintenance: 'Argus 维护', learning: '资料学习',
};
export function libraryVertical(value: string, locale: string): string {
  return locale === 'zh-CN' && Object.hasOwn(verticals, value) ? verticals[value] : value;
}
