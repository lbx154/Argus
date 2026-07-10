import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { EventMsg } from '../api';
import { renderEvent, toneColor, isReasoning, eventKey, mergeFragment, type Rendered } from '../lib/eventRender';
import { theme } from '../lib/theme';
import { clockOf } from '../lib/format';
import { rotate, IDLE_LINES } from '../lib/soul';
import { PanelHeader, EmptyHint } from './primitives';
import {
  EVENT_VIEW_FILTERS,
  eventMatchesView,
  type EventViewFilter,
} from '../../../core/src/events';

const FILTER_LABEL: Record<EventViewFilter, string> = {
  all: 'all',
  attention: 'watch',
  milestones: 'milestones',
  messages: 'messages',
};

function EventRow({ ev, r }: { ev: EventMsg; r: Rendered }) {
  const roleHue = theme.role[r.role] ?? theme.inkFaint;
  const color = toneColor(r.tone);
  return (
    <div
      className={`group flex flex-wrap gap-x-2 gap-y-0.5 border-b border-line/30 px-3 py-1.5 font-mono text-[12px] leading-relaxed last:border-b-0 sm:flex-nowrap ${r.reasoning ? 'opacity-70' : ''}`}
      style={r.rule ? { borderTop: `1px solid ${roleHue}22`, marginTop: 3, paddingTop: 5 } : undefined}
    >
      <span className="shrink-0 select-none text-ink-faint tabular-nums">{clockOf(ev)}</span>
      <span
        className="w-[68px] shrink-0 truncate text-[10px] font-medium uppercase tracking-wide"
        style={{ color: roleHue }}
        title={r.label}
      >
        {r.label}
      </span>
      <span className="shrink-0 select-none" style={{ color }}>
        {r.glyph}
      </span>
      <span className="min-w-0 basis-full whitespace-pre-wrap break-words sm:basis-auto" style={{ color }}>
        {r.text}
      </span>
    </div>
  );
}

/**
 * The live event feed — a CLEAN, whitelisted stream (matching the terminal
 * cockpit), not a raw event dump. Non-whitelisted events (agent_io.* framing,
 * telemetry, internal bookkeeping) are dropped; reasoning is hidden until ⌘O.
 * Auto-follows the tail unless the user scrolls up to read history.
 */
export function EventStream({
  events,
  connected,
  showReasoning,
  onToggleReasoning,
}: {
  events: EventMsg[];
  connected: boolean;
  showReasoning: boolean;
  onToggleReasoning: () => void;
}) {
  const [following, setFollowing] = useState(true);
  const [filter, setFilter] = useState<EventViewFilter>('all');
  const [query, setQuery] = useState('');
  const scroller = useRef<HTMLDivElement>(null);

  // render + whitelist + COALESCE streaming message fragments once per change.
  // engineer.progress message events stream in fragments sharing a message_id
  // (replace=True); the REPL collapses them to one line — we keep the longest
  // fragment at its first position so a streaming reply is ONE growing row, not
  // a char-by-char flood.
  const baseRows = useMemo(() => {
    const out: { ev: EventMsg; r: Rendered; key: string }[] = [];
    const msgRow = new Map<string, number>(); // message_id → index in out
    let hiddenReasoning = 0;
    events.forEach((ev, i) => {
      const r = renderEvent(ev);
      if (!r) return; // non-whitelisted → hidden
      if (r.reasoning && !showReasoning) {
        hiddenReasoning++;
        return;
      }
      const rec = ev as Record<string, unknown>;
      const mid = String(rec.message_id ?? '');
      const isMsg =
        !!mid &&
        String(rec.type) === 'engineer.progress' &&
        ['assistant_message', 'agent_message', 'message'].includes(String(rec.kind));
      if (isMsg && msgRow.has(mid)) {
        const idx = msgRow.get(mid)!;
        // grow the streaming message (merge blocks) instead of dropping shorter
        // fragments — a multi-block reply must not look truncated.
        out[idx] = { ...out[idx], r: { ...out[idx].r, text: mergeFragment(out[idx].r.text, r.text) } };
        return;
      }
      const entry = { ev, r, key: eventKey(ev, i) };
      if (isMsg) msgRow.set(mid, out.length);
      out.push(entry);
    });
    return { list: out, hiddenReasoning };
  }, [events, showReasoning]);

  const rows = useMemo(() => ({
    ...baseRows,
    list: baseRows.list.filter(({ ev, r }) => eventMatchesView(ev, r, filter, query)),
  }), [baseRows, filter, query]);

  const reasoningTotal = useMemo(() => events.filter(isReasoning).length, [events]);

  useLayoutEffect(() => {
    if (following && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [rows.list.length, following, filter, query]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const jump = () => {
    setFollowing(true);
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  };

  return (
    <section className="card relative flex min-h-0 flex-1 flex-col">
      <PanelHeader
        title="Live feed"
        right={
          <div className="flex items-center gap-3">
            <span className="text-[10px] text-ink-faint">
              {rows.list.length}{rows.list.length !== baseRows.list.length ? `/${baseRows.list.length}` : ''} shown
            </span>
            <button
              onClick={onToggleReasoning}
              className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
                showReasoning ? 'text-blue-sky' : 'text-ink-faint hover:text-ink-dim'
              }`}
              title="toggle agent reasoning (⌘O)"
            >
              reasoning{reasoningTotal ? ` ·${reasoningTotal}` : ''}
            </button>
            <span className={`text-[10px] ${connected ? 'text-ok' : 'text-ink-faint'}`}>
              {connected ? '● live' : '○ reconnecting'}
            </span>
          </div>
        }
      />
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
        <label className="relative min-w-[130px] flex-1">
          <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[11px] text-ink-faint">/</span>
          <span className="sr-only">Search live feed</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search feed"
            className="h-7 w-full rounded border border-line bg-bg/50 pl-6 pr-2 font-mono text-[11px] text-ink outline-none placeholder:text-ink-faint focus:border-blue-deep"
          />
        </label>
        <div className="flex max-w-full gap-1 overflow-x-auto scroll-thin" role="group" aria-label="Filter live feed">
          {EVENT_VIEW_FILTERS.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
              className={`shrink-0 rounded border px-2 py-1 text-[10px] transition-colors ${
                filter === value
                  ? value === 'attention'
                    ? 'border-warn/50 bg-warn/10 text-warn'
                    : 'border-blue-deep/50 bg-blue-deep/15 text-blue-sky'
                  : 'border-transparent text-ink-faint hover:border-line hover:text-ink-dim'
              }`}
            >
              {FILTER_LABEL[value]}
            </button>
          ))}
        </div>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto scroll-thin py-1.5">
        {rows.list.length === 0 ? (
          <EmptyHint>
            {query || filter !== 'all' ? 'no events match this view' : rotate(IDLE_LINES)}
          </EmptyHint>
        ) : (
          rows.list.map(({ ev, r, key }) => <EventRow key={key} ev={ev} r={r} />)
        )}
      </div>
      {!following && (
        <button
          onClick={jump}
          className="absolute bottom-3 right-4 rounded border border-line bg-surface px-3 py-1 text-[11px] text-ink-dim shadow-glow transition-colors hover:border-ink-faint hover:text-ink"
        >
          ↓ jump to latest
        </button>
      )}
    </section>
  );
}
