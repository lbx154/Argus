import { useState } from 'react';
import { questionRole, type OperatorDecisionCard } from '../../../core/src/decisions';
import { useI18n } from '../i18n';
import { roleLabel } from '../lib/enumLabels';
import { isImeComposing } from '../lib/ime';
import { questionSentence } from '../lib/taskLanguage';

/** The only pending-question surface. Reads snapshot.pending_questions' operator_decision
 * (id, item_id, question), with task_title joined from snapshot.backlog by item_id. */
export function PendingBanner({ reply, busy, error, note, onNoteChange, onSubmit }: {
  reply: OperatorDecisionCard;
  busy: boolean;
  error: string;
  note: string;
  onNoteChange: (note: string) => void;
  onSubmit: (note: string) => Promise<boolean>;
}) {
  const { t, locale } = useI18n();
  const [expanded, setExpanded] = useState(Boolean(note));
  const zh = locale === 'zh-CN';
  const role = roleLabel(questionRole(reply), t);
  const task = reply.task_title;
  const question = questionSentence(role, task, reply.question, zh);
  const submit = async () => {
    if (!busy && note.trim()) await onSubmit(note);
  };
  return <section className="mb-2 rounded-md border border-gold/40 bg-gold/5 px-3 py-2 text-xs" data-pending-question={reply.id}>
    <div className="flex items-start gap-3">
      <p className="max-h-[24vh] min-w-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words leading-5 text-ink">{question}</p>
      {!expanded && <button type="button" className="shrink-0 py-0.5 font-medium text-gold" onClick={() => setExpanded(true)}>{t('pending.reviewRespond')}</button>}
    </div>
    {expanded && <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={event => { event.preventDefault(); void submit(); }}>
      <textarea aria-label={t('decision.notePlaceholder')} placeholder={t('decision.notePlaceholder')} rows={2}
        value={note} onChange={event => onNoteChange(event.target.value)} disabled={busy}
        onKeyDown={event => {
          if (!isImeComposing(event) && event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            event.preventDefault(); void submit();
          }
        }} className="min-w-0 flex-1 resize-y rounded border border-line bg-bg px-2 py-1 text-sm text-ink outline-none focus:border-blue disabled:opacity-60" />
      <button type="submit" disabled={busy || !note.trim()} className="rounded border border-gold/40 px-2 py-1 text-gold disabled:opacity-50">{t(busy ? 'decision.applying' : 'decision.sendAnswer')}</button>
    </form>}
    {error && <p role="alert" className="mt-1 text-err">{error}</p>}
  </section>;
}
