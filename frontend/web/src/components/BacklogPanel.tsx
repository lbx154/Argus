import { useState } from 'react';
import { useI18n } from '../i18n';
import type { BacklogSummary } from '../../../core/src/types';
import { PanelHeader, EmptyHint, Chip } from './primitives';
import { isRepeating, visibleBacklogItems } from '../../../core/src/backlog';
import { statusLabel } from '../lib/enumLabels';

const STATUS_COLOR: Record<string, string> = {
  in_progress: 'rgb(var(--blue))',
  running: 'rgb(var(--blue))',
  pending: 'rgb(var(--ink-faint))',
  queued: 'rgb(var(--ink-faint))',
  done: 'rgb(var(--blue))',
  completed: 'rgb(var(--blue))',
  blocked: 'rgb(var(--err))',
  failed: 'rgb(var(--err))',
};

/** The project's tasks as they stand; a task's details open from its title. */
export function BacklogPanel({
  items,
  onInspect,
}: {
  items: BacklogSummary[];
  onInspect?: (id: string) => void;
}) {
  const { t } = useI18n();
  const [showHistory, setShowHistory] = useState(false);
  const active = visibleBacklogItems(items, false);
  const history = visibleBacklogItems(items, true);
  const shown = showHistory ? history : active;
  return (
    <section className={`card flex flex-col ${shown.length > 0 ? 'min-h-0 flex-1' : 'shrink-0'}`}>
      <PanelHeader
        title={t('panel.backlog')}
        right={
          <button
            className="text-[10px] text-ink-faint transition-colors hover:text-ink"
            onClick={() => setShowHistory((value) => !value)}
          >
            {t(showHistory ? 'backlog.active' : 'backlog.history', { count: showHistory ? active.length : history.length })}
          </button>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
        {shown.length === 0 && (
          <EmptyHint>{t(showHistory ? 'backlog.noHistory' : 'backlog.empty')}</EmptyHint>
        )}
        {shown.map((it) => {
          const color = STATUS_COLOR[it.status] ?? 'rgb(var(--ink-faint))';
          return (
            <div key={it.id} className="min-w-0 border-b border-line/60 px-3 py-2 last:border-0">
              <button
                type="button"
                onClick={() => onInspect?.(it.id)}
                disabled={!onInspect}
                className="block max-w-full truncate text-left text-xs font-medium text-ink enabled:hover:text-blue-sky enabled:focus-visible:outline-none enabled:focus-visible:underline"
                title={onInspect ? t('backlog.viewDetails') : undefined}
              >
                {it.title}
              </button>
              <div className="mt-0.5 flex items-center gap-1.5">
                <Chip color={color}>{statusLabel(it.status, t)}</Chip>
                {isRepeating(it) && <span className="text-[10px] text-blue-sky">↻ {t('backlog.iterating')}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
