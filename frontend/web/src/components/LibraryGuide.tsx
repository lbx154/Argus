import type { SkillLibraryItem, WikiLibraryItem } from '../api';
import { useI18n } from '../i18n';
import { knowledgePurpose, knowledgeScopePurpose, type SkillGuide } from '../lib/libraryPresentation';
import { roleLabel } from '../lib/enumLabels';

export function SkillReadingGuide({ guide, item }: { guide: SkillGuide; item: SkillLibraryItem }) {
  const { t } = useI18n();
  return <section data-skill-reading-guide className="mt-4 rounded-lg border border-line/60 bg-bg/60 p-4 text-sm leading-relaxed text-ink">
    <h4 className="font-semibold">这项方法有什么用</h4>
    <p className="mt-2">{guide.summary}</p>
    <dl className="mt-3 space-y-2 text-xs text-ink-dim">
      <div><dt className="font-medium">什么时候参考</dt><dd>任务用得上时会自动参考。</dd></div>
      <div><dt className="font-medium">适用角色</dt><dd>{roleLabel(item.role, t)}</dd></div>
      <div><dt className="font-medium">需要注意</dt><dd>前置条件和软件许可以原文为准。</dd></div>
    </dl>
  </section>;
}

export function KnowledgeReadingGuide({ item }: { item: WikiLibraryItem }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  return <section data-knowledge-reading-guide className="mt-4 rounded-lg border border-line/60 bg-bg/60 p-3 text-xs leading-relaxed text-ink-dim">
    <h4 className="font-semibold text-ink">{zh ? '这类知识怎样帮助工作' : 'How this kind of knowledge helps'}</h4>
    <p className="mt-2">{knowledgePurpose(item.kind, locale)}</p>
    <p className="mt-2">{knowledgeScopePurpose(item.scope, locale)}</p>
  </section>;
}
