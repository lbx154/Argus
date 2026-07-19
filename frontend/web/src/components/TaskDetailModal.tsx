import { useBacklogItem } from '../hooks';
import { isTerminalBacklogItem } from '../../../core/src/backlog';
import { Modal } from './Modal';
import { Button, Chip, Spinner } from './primitives';

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
  onDone,
  onSkip,
  onStop,
  busy,
  readOnly = false,
}: {
  sid: string | null;
  itemId: string | null;
  onClose: () => void;
  onDone: (id: string) => void;
  onSkip: (id: string) => void;
  onStop: (id: string) => void;
  busy: boolean;
  readOnly?: boolean;
}) {
  const query = useBacklogItem(sid, itemId);
  const item = query.data;
  const terminal = item ? isTerminalBacklogItem(item) : false;
  return (
    <Modal open={Boolean(itemId)} onClose={onClose} label="Task details" width="max-w-3xl">
      <div className="flex flex-wrap items-start gap-3 border-b border-line px-4 py-3 sm:flex-nowrap sm:px-5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-sm font-semibold text-ink">{item?.title || 'Task details'}</h2>
            {item ? <Chip>{item.status}</Chip> : null}
          </div>
          <p className="mt-0.5 font-mono text-[10px] text-ink-faint">{itemId}</p>
        </div>
        {!readOnly && item && !terminal ? (
          <div className="order-3 flex w-full shrink-0 items-center justify-end gap-1 sm:order-none sm:w-auto">
            {item.iterate ? <Button onClick={() => onStop(item.id)} disabled={busy}>stop loop</Button> : null}
            <Button onClick={() => onDone(item.id)} disabled={busy}>done</Button>
            <Button variant="danger" onClick={() => onSkip(item.id)} disabled={busy}>skip</Button>
          </div>
        ) : null}
        <button
          type="button"
          aria-label="close task details"
          onClick={onClose}
          className="order-2 rounded-md px-2 py-1 text-lg leading-none text-ink-faint hover:bg-surface hover:text-ink sm:order-none"
        >×</button>
      </div>
      <div className="max-h-[70vh] overflow-y-auto p-4 scroll-thin sm:p-5">
        {query.isLoading ? <div className="flex justify-center py-12"><Spinner /></div> : null}
        {query.isError ? (
          <div className="rounded-md border border-err/40 bg-err/5 p-3 text-xs text-err">
            {(query.error as Error).message}
          </div>
        ) : null}
        {item ? (
          <div className="space-y-4">
            {item.pending_question ? (
              <div className="rounded-lg border border-warn/40 bg-warn/5 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-wider text-warn">Waiting on you</div>
                <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink">{item.pending_question}</p>
              </div>
            ) : null}
            <section>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">Objective</div>
              <div className="whitespace-pre-wrap rounded-lg border border-line bg-bg/50 p-3 text-sm leading-relaxed text-ink-dim">
                {item.objective || item.original_objective || '(no objective recorded)'}
              </div>
            </section>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Metric label="priority" value={`p${item.priority}`} />
              <Metric label="started" value={when(item.started_ts)} />
              <Metric label="finished" value={when(item.finished_ts)} />
            </div>
            {item.iterate || item.iteration_cycles_done || item.iteration_cost_usd ? (
              <section>
                <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">Iteration</div>
                <div className="grid grid-cols-3 gap-2">
                  <Metric label="mode" value={item.iterate ? 'auto-iterate' : 'single pass'} />
                  <Metric label="cycles" value={`${item.iteration_cycles_done ?? 0}/${item.iteration_max_cycles ?? '—'}`} />
                  <Metric label="cost" value={`$${(item.iteration_cost_usd ?? 0).toFixed(2)}`} />
                </div>
              </section>
            ) : null}
            {item.last_error ? (
              <section className="rounded-lg border border-err/30 bg-err/5 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-wider text-err">Last error</div>
                <p className="mt-1 whitespace-pre-wrap font-mono text-xs leading-relaxed text-ink-dim">{item.last_error}</p>
              </section>
            ) : null}
            {item.notes ? (
              <section>
                <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">Notes</div>
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-ink-dim">{item.notes}</p>
              </section>
            ) : null}
            {(item.tags?.length || item.deps?.length) ? (
              <div className="flex flex-wrap gap-1.5">
                {(item.tags ?? []).map((tag) => <Chip key={`tag-${tag}`}>#{tag}</Chip>)}
                {(item.deps ?? []).map((dep) => <Chip key={`dep-${dep}`}>depends on {dep}</Chip>)}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
