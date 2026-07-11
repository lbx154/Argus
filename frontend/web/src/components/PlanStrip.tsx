import type { BacklogItem } from '../api';

const ACTIVE = new Set(['running', 'in_progress', 'claimed']);
const QUEUED = new Set(['pending', 'queued']);

export function PlanStrip({ items }: { items: BacklogItem[] }) {
  const active = items.find((item) => ACTIVE.has(item.status));
  const queued = items.filter((item) => QUEUED.has(item.status)).length;
  if (!active && queued === 0) return null;
  return (
    <div className="flex min-h-11 items-center gap-2 border-b border-line/70 px-3">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${active ? 'animate-pulse bg-engineer' : 'bg-ink-faint/50'}`} />
      <span className="min-w-0 flex-1 truncate text-xs text-ink-dim">
        {active?.title || active?.objective || 'Queued work'}
      </span>
      {queued ? <span className="shrink-0 font-mono text-xs text-ink-faint">+{queued}</span> : null}
    </div>
  );
}
