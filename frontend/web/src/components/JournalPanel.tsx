import type { JournalEntry } from '../api';
import { PanelHeader, EmptyHint } from './primitives';
import { ago, money } from '../lib/format';

const KIND_COLOR: Record<string, string> = {
  win: '#7fa386',
  milestone: '#c7a66a',
  insight: '#8fa7b8',
  decision: '#a69daf',
  failure: '#c77b72',
  note: '#7e7d75',
};

/** The research journal — the daemon's distilled memory of wins/insights. */
export function JournalPanel({ entries }: { entries: JournalEntry[] }) {
  const newestFirst = [...entries].reverse();
  return (
    <section className="card flex min-h-0 flex-1 flex-col">
      <PanelHeader title="Journal" right={<span className="text-[10px] text-ink-faint">{entries.length}</span>} />
      <div className="min-h-0 flex-1 overflow-y-auto scroll-thin">
        {entries.length === 0 && <EmptyHint>no journal entries yet</EmptyHint>}
        {newestFirst.map((e) => {
          const color = KIND_COLOR[e.kind] ?? '#8a93a6';
          return (
            <div key={e.id} className="border-b border-line/60 px-3 py-2 last:border-0">
              <div className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
                <span className="text-[10px] uppercase tracking-wide" style={{ color }}>
                  {e.kind}
                </span>
                <span className="ml-auto text-[10px] text-ink-faint">{ago(e.ts)}</span>
              </div>
              <div className="mt-1 text-xs font-medium text-ink">{e.title}</div>
              {e.summary && <div className="mt-0.5 text-[11px] leading-snug text-ink-dim">{e.summary}</div>}
              <div className="mt-1 flex flex-wrap items-center gap-1">
                {(e.tags ?? []).slice(0, 4).map((t) => (
                  <span key={t} className="rounded bg-line/60 px-1 text-[9px] text-ink-faint">
                    {t}
                  </span>
                ))}
                {typeof e.cost_usd === 'number' && e.cost_usd > 0 && (
                  <span className="ml-auto text-[10px] text-ink-faint">{money(e.cost_usd)}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
