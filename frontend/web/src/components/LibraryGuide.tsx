import type { SkillLibraryItem, WikiLibraryItem } from '../api';
import { useI18n } from '../i18n';
import { knowledgePurpose, knowledgeScopePurpose, type SkillGuide } from '../lib/libraryPresentation';
import { roleLabel } from '../lib/enumLabels';

export function LibraryIntroduction({ kind }: { kind: 'skills' | 'knowledge' }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  return <div className="shrink-0 border-b border-line/60 bg-bg/50 px-4 py-3 text-xs leading-relaxed text-ink-dim" data-library-introduction={kind}>
    <p>{kind === 'skills'
      ? zh ? '技能库告诉 Argus「怎么做」：可复用的方法、步骤和使用条件。查看不等于执行，技能也不是需要逐个安装的插件。' : 'Skills describe how to work: reusable methods, steps and conditions. Reading a skill does not execute it or install a plugin.'
      : zh ? '知识库记录「知道什么、学到什么」：事实、经验和资料。Argus 可在工作中按需参考；已保存或被读取不代表结论已经验证。' : 'Knowledge records what was learned: facts, lessons and reference material. Saved or recalled information is not automatically verified.'}</p>
    <p className="mt-1 text-ink-faint">{zh ? '内置说明提供中文导读；你、Agent 或第三方写下的原文保留，不会在这里自动翻译或改写。' : 'Bundled material has a Chinese reading guide in Chinese mode. User, Agent and third-party originals are not automatically translated or rewritten here.'}</p>
  </div>;
}

export function SkillReadingGuide({ guide, item }: { guide: SkillGuide; item: SkillLibraryItem }) {
  const { t } = useI18n();
  return <section data-skill-reading-guide className="mt-4 rounded-lg border border-line/60 bg-bg/60 p-4 text-sm leading-relaxed text-ink">
    <h4 className="font-semibold">这项方法有什么用</h4>
    <p className="mt-2">{guide.summary}</p>
    <dl className="mt-3 space-y-2 text-xs text-ink-dim">
      <div><dt className="font-medium">什么时候参考</dt><dd>当前任务与上面的用途相符时，由既有任务与技能选择流程按需读取；浏览本页不会运行工具或发起模型任务。</dd></div>
      <div><dt className="font-medium">目录标注的角色</dt><dd>{roleLabel(item.role, t)}；这只是说明，不调整角色职责或权限。</dd></div>
      <div><dt className="font-medium">需要注意</dt><dd>这是随包版本的中文导读，不是完整执行指令，也不是已经完成或验证的证据。具体步骤、前置条件、软件许可和授权限制请对照原文。</dd></div>
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
    <p className="mt-2 text-ink-faint">{zh ? '以上解释条目类型与范围，不是这篇正文的翻译或真实性证明。具体内容、来源和适用条件仍以原文为准。' : 'This explains the kind and scope, not the page itself or its validity. Consult the original content, sources and conditions.'}</p>
  </section>;
}
