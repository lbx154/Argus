import type { SkillLibraryItem, WikiPageKind, WikiScope } from '../api';
import chinese from './bundledSkillChinese.json';

export interface SkillGuide { title: string; summary: string; originalName: string; source: string; sourceSha256: string }
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
  fact: ['记录可核对的环境、对象或事实，供后续工作查证；记录本身不保证事实仍然有效。', 'Records checkable facts for later verification; a saved record is not a guarantee that it is still current.'],
  lesson: ['保留工作中得到的经验和问题，帮助类似任务少走弯路；不能代替当前证据或评审。', 'Keeps lessons from work to inform similar tasks; it does not replace current evidence or review.'],
  survey: ['整理问答或调研材料与来源，为后续查阅提供线索；结论仍需结合原文核对。', 'Organizes answers or research and their sources for later reference; check conclusions against the original evidence.'],
  principles: ['归纳可反复参考的建议，帮助选择方法；应检查所引用的经验与适用条件。', 'Distills recurring guidance for choosing methods; check the cited lessons and applicability.'],
  note: ['保存关于你的背景、偏好或计划，帮助后续任务理解上下文；不是工具使用授权。', 'Keeps personal context, preferences or plans for later work; it is not authorization to use tools.'],
  profile: ['汇总 Argus 对你的背景与偏好的了解，方便核对是否准确；不代表公开资料或任务完成情况。', 'Summarizes personal context and preferences for inspection; it is neither a public record nor a task-completion report.'],
  page: ['一般参考资料。具体用途取决于原文和来源，不能仅凭“已保存”判断内容可靠或适用。', 'General reference material. Its use depends on the original content and sources, not merely on being saved.'],
};
export function knowledgePurpose(kind: string, locale: string): string {
  const row = Object.hasOwn(knowledge, kind) ? knowledge[kind as WikiPageKind] : knowledge.page;
  return row[locale === 'zh-CN' ? 0 : 1];
}
const scopeHelp: Record<WikiScope, [string, string]> = {
  private: ['你的个人背景资料，与共享知识分开保存。这里不改变既有读取权限或模型的数据处理方式。', 'Personal context is stored separately from shared knowledge. This view does not change existing access or model data handling.'],
  global: ['跨项目参考的通用资料；不要把个人信息当作公共知识放在这里。', 'General reference across projects; personal information does not belong in shared knowledge.'],
  vertical: ['同一领域可复用的专门资料，使用前仍要核对当前任务的条件。', 'Domain-specific reference; verify that it applies to the current task.'],
  project: ['围绕当前项目保存的资料与背景；分类不是操作系统沙箱或文件权限边界。', 'Material associated with this project; the category is not an OS sandbox or file-permission boundary.'],
};
export function knowledgeScopePurpose(scope: WikiScope, locale: string): string {
  if (!Object.hasOwn(scopeHelp, scope)) return locale === 'zh-CN' ? '范围尚未确认，请以来源说明为准。' : 'Scope not confirmed; consult the source.';
  return scopeHelp[scope][locale === 'zh-CN' ? 0 : 1];
}
