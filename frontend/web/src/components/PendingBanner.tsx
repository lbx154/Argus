import { questionRole, type OperatorDecisionCard } from '../../../core/src/decisions';
import { useI18n } from '../i18n';
import { roleLabel } from '../lib/enumLabels';
import { questionSentence } from '../lib/taskLanguage';

/** Explains the question answered by the main composer. Reads snapshot.pending_questions'
 * operator_decision, with task_title joined from snapshot.backlog by item_id. */
export function PendingBanner({ reply }: { reply: OperatorDecisionCard }) {
  const { t, locale } = useI18n();
  const question = questionSentence(roleLabel(questionRole(reply), t), reply.task_title, reply.question, locale === 'zh-CN');
  return <section className="mb-2 rounded-md border border-gold/40 bg-gold/5 px-3 py-2 text-xs" data-pending-question={reply.id}>
    <p className="max-h-[24vh] overflow-y-auto whitespace-pre-wrap break-words leading-5 text-ink">{question}</p>
  </section>;
}
