import { ErrorMessage } from './ErrorMessage';
import { useBacklogItem } from '../hooks';
import { currentOutcome } from '../../../core/src/backlog';
import { MarkdownContent } from './MarkdownContent';
import { Modal } from './Modal';
import { Chip, RawDisclosure, Spinner } from './primitives';
import { useI18n } from '../i18n';
import { outcomeLabels, statusLabel } from '../lib/enumLabels';

const when = (ts: number | null | undefined): string =>
  ts ? new Date(ts * 1000).toLocaleString() : '—';

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line/70 bg-bg/40 px-3 py-2">
      <div className="text-[9px] font-semibold uppercase tracking-wider text-ink-faint">{label}</div>
      <div className="mt-0.5 text-xs text-ink-dim">{value}</div>
    </div>
  );
}

/** Full task contract behind the compact backlog row. */
export function TaskDetailModal({
  sid,
  itemId,
  onClose,
}: {
  sid: string | null;
  itemId: string | null;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const query = useBacklogItem(sid, itemId);
  const item = query.data;
  // A task a newer plan replaced came to that; its last attempt's pause and error are not its result.
  const replaced = item?.status === 'superseded';
  const outcome = replaced ? [statusLabel('superseded', t)] : outcomeLabels(item && currentOutcome(item), t);
  const objective = <div className="mt-1 rounded-lg border border-line bg-bg/50 p-3 text-sm leading-relaxed text-ink-dim">
    <MarkdownContent sid={sid}>{item?.objective || item?.original_objective || t('task.noObjective')}</MarkdownContent>
  </div>;
  return (
    <Modal open={Boolean(itemId)} onClose={onClose} label={t('task.details')} width="max-w-3xl" showClose={false}>
      <div className="flex items-start gap-3 border-b border-line px-4 py-3 sm:px-5">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="min-w-0 break-words text-sm font-semibold text-ink">{item?.title || t('task.details')}</h2>
            {item ? <Chip>{statusLabel(item.status, t)}</Chip> : null}
          </div>
        </div>
        <button
          type="button"
          aria-label={t('task.close')}
          onClick={onClose}
          className="rounded-md px-2 py-1 text-lg leading-none text-ink-faint hover:bg-surface hover:text-ink"
        >×</button>
      </div>
      <div className="max-h-[70vh] overflow-y-auto p-4 scroll-thin sm:p-5">
        {query.isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : null}
        {query.isError ? (
          <div className="rounded-md border border-err/40 bg-err/5 p-3 text-xs text-err">
            <ErrorMessage error={query.error} />
          </div>
        ) : null}
        {item ? (
          <div className="space-y-4">
            {item.pending_question ? (
              <div className="rounded-lg border border-warn/40 bg-warn/5 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-wider text-warn">{t('task.waitingOnYou')}</div>
                <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink">{item.pending_question}</p>
              </div>
            ) : null}
            {/* A task the Planner wrote has no request of the reader's own: its
                title says what it is, and the brief is the team's to read. */}
            {item.plan_id ? <RawDisclosure label={t('task.teamBrief')}>{objective}</RawDisclosure> : (
              <section>
                <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('task.objective')}</div>
                {objective}
              </section>
            )}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Metric label={t('task.started')} value={when(item.started_ts)} />
              <Metric label={t('task.finished')} value={when(item.finished_ts)} />
            </div>
            {outcome.length ? (
              <section>
                <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('task.outcome')}</div>
                <div className="flex flex-wrap gap-1.5">
                  {outcome.map((row) => <Chip key={row}>{row}</Chip>)}
                </div>
              </section>
            ) : null}
            {item.iteration_cycles_done || item.iteration_cost_usd ? (
              <section>
                <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('task.iteration')}</div>
                <div className="grid grid-cols-3 gap-2">
                  <Metric label={t('task.mode')} value={item.iterate ? t('task.autoIterate') : t('task.singlePass')} />
                  <Metric label={t('task.cycles')} value={`${item.iteration_cycles_done ?? 0}${item.iteration_max_cycles ? `/${item.iteration_max_cycles}` : ''}`} />
                  <Metric label={t('task.cost')} value={`$${(item.iteration_cost_usd ?? 0).toFixed(2)}`} />
                </div>
              </section>
            ) : null}
            {item.last_error && !replaced ? (
              <section className="rounded-lg border border-err/30 bg-err/5 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-wider text-err">{t('task.lastError')}</div>
                <div className="mt-1 text-xs leading-relaxed text-ink-dim"><ErrorMessage error={item.last_error} code="agent_failed" /></div>
              </section>
            ) : null}
            {item.notes ? (
              <section>
                <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{t('task.notes')}</div>
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-ink-dim">{item.notes}</p>
              </section>
            ) : null}
            {/* Tags are the team's routing marks, never shown to the reader. */}
            {item.deps?.length ? (
              <div className="flex flex-wrap gap-1.5">
                <Chip>{t('task.dependsOnCount', { count: item.deps.length })}</Chip>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
