import type { ReactNode } from 'react';
import { Button } from '../components/primitives';
import { useI18n } from '../i18n';
import type { FoundationDraft } from './foundation';

/** The same question editor is used for a foundation and a retained progress card. */
export function ReadingQuestionEditor({ draft, onChange, onSubmit, disabled, children }: {
  draft: FoundationDraft; onChange: (draft: FoundationDraft) => void; onSubmit: () => void;
  disabled?: boolean; children?: ReactNode;
}) {
  const zh = useI18n().locale === 'zh-CN';
  const question = !!draft.parentId || !!draft.progressSource;
  return <>
    {children}
    <textarea aria-label={zh ? '想理解的问题' : 'Question to understand'} className="min-h-40 w-full rounded-lg border border-line bg-bg p-3 text-sm leading-6 text-ink"
      maxLength={8000} value={draft.question} onChange={event => onChange({ ...draft, question: event.target.value })} />
    {draft.sourceTitle ? <p className="mt-2 text-xs text-ink-faint">{zh ? '从这项任务打开：' : 'Opened from this task: '}{draft.sourceTitle}</p> : null}
    <div className="mt-3 flex justify-end"><Button data-reading-submit disabled={!draft.question.trim() || disabled} onClick={onSubmit}>
      {question ? zh ? '提问' : 'Ask question' : zh ? '生成并保存基础说明' : 'Generate and save foundations'}
    </Button></div>
  </>;
}
