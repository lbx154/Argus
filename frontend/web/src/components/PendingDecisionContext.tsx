import type { OperatorDecisionCard } from '../../../core/src/decisions';
import { useI18n } from '../i18n';
import { statusLabel } from '../lib/enumLabels';
import { formatRelativeTime } from '../lib/format';
import { Chip } from './primitives';

/** The same task attribution accompanies its banner and reply dialog. */
export function PendingDecisionContext({ card }: { card: OperatorDecisionCard }) {
  const { t, locale } = useI18n();
  const askedAt = typeof card.asked_at === 'number' && Number.isFinite(card.asked_at) && card.asked_at > 0
    ? new Date(card.asked_at * 1000) : null;
  const paused = card.task_status?.startsWith('paused');
  return <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-ink-faint" data-decision-task={card.item_id}>
    {card.task_title ? <span className="min-w-0 basis-full break-words text-xs leading-relaxed text-ink-dim" title={card.task_title}>
      {t('decision.taskName', { title: card.task_title })}
    </span> : null}
    {card.kind !== 'domain_intake' && card.is_current_task != null ? <span>{t(card.is_current_task ? 'decision.currentTask' : 'decision.otherTask')}</span> : null}
    <Chip>{t('decision.awaitingReply')}</Chip>
    {card.task_status ? <span>{paused ? t('label.status.paused') : statusLabel(card.task_status, t)}</span> : null}
    {askedAt && !Number.isNaN(askedAt.getTime()) ? <time dateTime={askedAt.toISOString()} title={askedAt.toLocaleString(locale)}>
      {t('decision.askedAt', { time: formatRelativeTime(askedAt, locale) })}
    </time> : null}
  </div>;
}
