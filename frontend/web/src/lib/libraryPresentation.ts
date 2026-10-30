import type { SkillLibraryItem } from '../api';
export function skillPresentation(item: SkillLibraryItem, locale: string) {
  return { title: item.name,
    summary: locale === 'zh-CN' && !/[\u3400-\u9fff]/.test(item.description) ? '' : item.description };
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
