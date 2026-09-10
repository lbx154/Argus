import type { DeliveryReceipt } from '../../../core/src/types';
import { useI18n } from '../i18n';

export function DeliveryNotice({
  delivery,
  onOpen,
  onDismiss,
}: {
  delivery: DeliveryReceipt;
  onOpen: (delivery: DeliveryReceipt) => void;
  onDismiss: (deliveryId: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const certified = delivery.kind === 'submission_certified';
  const heading = certified
    ? (zh ? '交付已认证' : 'Delivery certified')
    : (zh ? '任务已完成' : 'Task completed');
  const target = delivery.primary_target;

  return (
    <aside
      role="status"
      aria-live="polite"
      className="fixed right-4 top-4 z-[90] w-[min(30rem,calc(100vw-2rem))] rounded-xl border border-ok/40 bg-panel/95 p-4 shadow-2xl backdrop-blur"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ok/15 text-sm font-bold text-ok">✓</span>
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ok">{heading}</div>
          <div className="mt-1 truncate text-sm font-semibold text-ink" title={delivery.title}>{delivery.title}</div>
          {delivery.summary ? <p className="mt-1 line-clamp-3 text-xs leading-5 text-ink-dim">{delivery.summary}</p> : null}
          {delivery.review_status && delivery.review_status !== 'not_assessed' ? (
            <div className="mt-2 font-mono text-[10px] text-ink-faint">
              Reviewer · {delivery.review_status}
            </div>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => { onOpen(delivery); onDismiss(delivery.delivery_id); }}
              className="rounded-md border border-ok/45 bg-ok/10 px-2.5 py-1.5 text-xs font-medium text-ok hover:border-ok hover:bg-ok/15"
            >
              {target ? (zh ? '打开成果' : 'Open result') : (zh ? '查看任务' : 'View task')}
            </button>
            <button
              type="button"
              onClick={() => onDismiss(delivery.delivery_id)}
              className="rounded-md px-2.5 py-1.5 text-xs text-ink-faint hover:bg-bg hover:text-ink"
            >
              {zh ? '稍后查看' : 'Later'}
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
