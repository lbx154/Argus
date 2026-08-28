import { useState } from 'react';
import { useI18n } from '../i18n';
import type { BacklogItem } from '../api';
import { PanelHeader, Button, EmptyHint, Chip } from './primitives';
import { visibleBacklogItems } from '../../../core/src/backlog';
import { priorityLabel, statusLabel } from '../lib/enumLabels';

const STATUS_COLOR: Record<string, string> = {
  in_progress: '#8fa7b8',
  running: '#8fa7b8',
  pending: '#7e7d75',
  queued: '#7e7d75',
  done: '#7fa386',
  completed: '#7fa386',
  blocked: '#c77b72',
  failed: '#c77b72',
};

/**
 * The backlog table with per-item disposition. Mutations route through the
 * daemon's flock-guarded `add_backlog_item`/dispose paths (never raw writes).
 */
export function BacklogPanel({
  items,
  onDispose,
  onStop,
  onInspect,
  busy,
  readOnly = false,
}: {
  items: BacklogItem[];
  onDispose: (id: string, op: 'done' | 'skip' | 'rm') => void;
  onStop: (id: string) => void;
  onInspect?: (id: string) => void;
  busy: boolean;
  readOnly?: boolean;
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
          const color = STATUS_COLOR[it.status] ?? '#8a93a6';
          const iterating = it.iterate;
          return (
            <div key={it.id} className="group border-b border-line/60 px-3 py-2 last:border-0">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <button
                    type="button"
                    onClick={() => onInspect?.(it.id)}
                    disabled={!onInspect}
                    className="block max-w-full truncate text-left text-xs font-medium text-ink enabled:hover:text-blue-sky enabled:focus-visible:outline-none enabled:focus-visible:underline"
                    title={onInspect ? t('backlog.viewDetails') : undefined}
                  >
                    {it.title || it.objective}
                  </button>
                  <div className="mt-0.5 flex items-center gap-1.5">
                    <Chip color={color}>{statusLabel(it.status, t)}</Chip>
                    {typeof it.priority === 'number' && (
                      <span className="text-[10px] text-ink-faint">{priorityLabel(it.priority, t)}</span>
                    )}
                    {iterating && <span className="text-[10px] text-blue-sky">↻ {t('backlog.iterating')}</span>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                  {!readOnly && iterating && (
                    <Button variant="ghost" onClick={() => onStop(it.id)} disabled={busy} title={t('backlog.stopIterating')}>
                      {t('backlog.stop')}
                    </Button>
                  )}
                  {!readOnly && (
                    <>
                      <Button variant="ghost" onClick={() => onDispose(it.id, 'done')} disabled={busy} title={t('backlog.markDone')}>
                        ✓
                      </Button>
                      <Button variant="ghost" onClick={() => onDispose(it.id, 'rm')} disabled={busy} title={t('backlog.remove')}>
                        ✕
                      </Button>
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
