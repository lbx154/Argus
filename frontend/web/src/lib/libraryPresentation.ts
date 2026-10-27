import type { SkillLibraryItem, WikiPageKind, WikiScope } from '../api';
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
  return { title: guide?.title ?? item.name, summary: guide?.summary ?? item.description, guide };
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
const knowledge: Record<WikiPageKind, [string, string]> = {
  fact: ['记录可核对的环境、对象或事实，供后续工作查证。', 'Records checkable facts for later verification.'],
  lesson: ['保留工作中得到的经验和问题，帮助类似任务少走弯路。', 'Keeps lessons from work to inform similar tasks.'],
  survey: ['整理问答或调研材料与来源，供后续查阅。', 'Organizes answers or research and their sources for later reference.'],
  principles: ['归纳可反复参考的建议，帮助选择方法。', 'Distills recurring guidance for choosing methods.'],
  note: ['保存关于你的背景、偏好或计划，帮助后续任务理解上下文。', 'Keeps personal context, preferences or plans for later work.'],
  profile: ['汇总 Argus 对你的背景与偏好的了解，方便核对是否准确。', 'Summarizes what Argus knows about your background and preferences, so you can check it.'],
  page: ['一般参考资料。', 'General reference material.'],
};
export function knowledgePurpose(kind: string, locale: string): string {
  const row = Object.hasOwn(knowledge, kind) ? knowledge[kind as WikiPageKind] : knowledge.page;
  return row[locale === 'zh-CN' ? 0 : 1];
}
const scopeHelp: Record<WikiScope, [string, string]> = {
  private: ['你的个人背景资料，与共享知识分开保存。', 'Personal context, stored separately from shared knowledge.'],
  global: ['跨项目参考的通用资料；不要把个人信息当作公共知识放在这里。', 'General reference across projects; personal information does not belong in shared knowledge.'],
  vertical: ['同一领域可复用的专门资料。', 'Domain-specific reference.'],
  project: ['围绕当前项目保存的资料与背景。', 'Material associated with this project.'],
};
export function knowledgeScopePurpose(scope: WikiScope, locale: string): string {
  if (!Object.hasOwn(scopeHelp, scope)) return locale === 'zh-CN' ? '范围未注明。' : 'Scope not recorded.';
  return scopeHelp[scope][locale === 'zh-CN' ? 0 : 1];
}
