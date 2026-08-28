import { operatorDecisionCards } from '../../../core/src/decisions';
import type { BacklogItem } from '../api';
import { useI18n } from '../i18n';

export function PendingBanner({
  questions,
  backlog,
  onAnswer,
}: {
  questions: Array<Record<string, unknown>>;
  backlog: BacklogItem[];
  onAnswer: () => void;
}) {
  const { t } = useI18n();
  const cards = operatorDecisionCards(
    questions,
    backlog as unknown as Array<Record<string, unknown>>,
  );
  if (!cards.length) return null;
  const card = cards[0];

  return (
    <div className="mb-2 flex min-h-11 items-center gap-3 rounded-md border border-gold/40 bg-gold/5 px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-gold">{card.title}</div>
        <div className="truncate text-xs text-ink-dim" title={card.reason || card.question}>
          {card.reason || card.question}
        </div>
      </div>
      {cards.length > 1 ? <span className="font-mono text-xs text-ink-faint">+{cards.length - 1}</span> : null}
      <button onClick={onAnswer} className="shrink-0 text-xs font-medium text-gold hover:text-gold-soft">
        {t('pending.reviewRespond')}
      </button>
    </div>
  );
}
