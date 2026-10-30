import { type OperatorDecisionCard } from '../../../core/src/decisions';

/** Explains the question answered by the main composer. Reads snapshot.pending_questions'
 * operator_decision, with task_title joined from snapshot.backlog by item_id. */
export function PendingBanner({ reply }: { reply: OperatorDecisionCard }) {
  return <section className="pending-question mb-2 rounded-md border border-gold/40 bg-gold/5 px-3 py-2 text-xs" data-pending-question={reply.id}>
    <p className="max-h-[24vh] overflow-y-auto whitespace-pre-wrap break-words leading-5 text-ink">{reply.question}</p>
  </section>;
}
