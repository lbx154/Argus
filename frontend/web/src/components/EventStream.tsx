import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { useGsapMotion } from '../lib/motion';
import type { ArtifactInfo, EventMsg } from '../api';
import type { DeliveryReceipt } from '../../../core/src/types';
import { renderEvent, toneColor, isReasoning, eventKey, mergeFragment, type Rendered } from '../lib/eventRender';
import { eventMatchesView, fragmentMode, type EventViewFilter } from '../../../core/src/events';
import { theme } from '../lib/theme';
import { clockOf } from '../lib/format';
import { PanelHeader, EmptyHint } from './primitives';
import { MarkdownContent } from './MarkdownContent';
import { ArgusMark } from './Wordmark';
import { useI18n } from '../i18n';
import { CopyButton } from './CopyButton';
import { roleLabel } from '../lib/enumLabels';

type ActivityRow = { ev: EventMsg; r: Rendered; key: string };
type ConversationGroup = { key: string; operator: ActivityRow; rows: ActivityRow[] };
const ROLE_ORDER = ['manager', 'planner', 'engineer', 'reviewer'] as const;
const RUNTIME_INFO_PATTERN = /Info: (?:Operation cancelled by user|Response was interrupted due to a server error\. Retrying\.\.\.)/gi;

export function activeProviderRequest(events: EventMsg[]): EventMsg | null {
  const active = new Map<string, EventMsg>();
  events.forEach((event) => {
    const type = String(event.type ?? '');
    if (type === 'life.mission.completed' || type === 'mission.completed') {
      active.clear();
      return;
    }
    const callId = String(event.call_id ?? '');
    if (!callId) return;
    if (type === 'provider.request.started') active.set(callId, event);
    else if (type === 'provider.request.completed' || type === 'provider.request.denied') active.delete(callId);
  });
  return Array.from(active.values()).at(-1) ?? null;
}

function EventRow({ ev, r, first, last }: { ev: EventMsg; r: Rendered; first: boolean; last: boolean }) {
  const roleHue = theme.role[r.role] ?? theme.inkFaint;
  const color = toneColor(r.tone);
  return (
    <div
      className={`event-activity-row group relative grid grid-cols-[16px_minmax(0,1fr)] gap-3 px-4 py-3 transition-colors hover:bg-bg/70 ${last ? 'animate-appear' : ''} ${r.reasoning ? 'opacity-60' : ''}`}
      style={r.rule ? { marginTop: 4 } : undefined}
    >
      <div className="relative flex justify-center">
        {!first ? <span className="absolute -top-2.5 h-4 w-px bg-line/60" /> : null}
        {!last ? <span className="absolute -bottom-2.5 top-2 w-px bg-line/60" /> : null}
        <span
          className="relative z-10 mt-1.5 h-2 w-2 rounded-full border-2 border-panel"
          style={{ backgroundColor: roleHue, boxShadow: `0 0 0 1px ${roleHue}55` }}
        />
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span
            className="truncate text-xs font-semibold uppercase tracking-[0.06em]"
            style={{ color: roleHue }}
            title={r.label}
          >
            {r.label}
          </span>
          <span className="text-xs" style={{ color }}>{r.glyph}</span>
          <time className="ml-auto font-mono text-xs tabular-nums text-ink-faint opacity-0 transition-opacity group-hover:opacity-100">
            {clockOf(ev)}
          </time>
        </div>
        <div className={`mt-0.5 whitespace-pre-wrap break-words text-sm leading-5 ${r.reasoning ? 'italic' : ''}`} style={{ color }}>
          {r.text}
        </div>
      </div>
    </div>
  );
}

function ConversationRow({
  ev,
  r,
  artifacts,
  onOpenArtifact,
}: {
  ev: EventMsg;
  r: Rendered;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
}) {
  const { t } = useI18n();
  const operator = String(ev.type) === 'ui.operator';
  const responseLatencyMs = Number(ev.response_latency_ms ?? 0);
  const responseLatency = !operator && responseLatencyMs >= 100
    ? ` · ${(responseLatencyMs / 1_000).toFixed(1)}s`
    : '';
  const rowRef = useRef<HTMLElement>(null);
  useGsapMotion(rowRef, (gsap, reduceMotion) => {
    if (!rowRef.current) return;
    if (reduceMotion) return;
    gsap.fromTo(
      rowRef.current,
      { autoAlpha: 0, x: operator ? 12 : 0, y: operator ? 0 : 8 },
      {
        autoAlpha: 1,
        x: 0,
        y: 0,
        duration: 0.28,
        ease: 'power2.out',
        clearProps: 'transform,opacity,visibility',
      },
    );
  });
  return (
    <article ref={rowRef} className="conversation-row group mx-auto w-full max-w-full px-4 py-3 sm:px-6 lg:max-w-[61.8vw]">
      {operator ? (
        <div className="flex items-end justify-end gap-2">
          <CopyButton
            text={r.text}
            label={t('copy.message')}
            copiedLabel={t('copy.copied')}
            className="opacity-60 sm:opacity-0 sm:group-hover:opacity-100"
          />
          <time className="shrink-0 pb-1 font-mono text-[10px] tabular-nums text-ink-faint">{clockOf(ev)}</time>
          <div className="max-w-[calc(100%_-_3rem)] rounded-[18px] bg-conversation-user px-4 py-2.5 text-[15px] leading-relaxed text-ink ring-1 ring-line/35 sm:max-w-[82%]">
            <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{r.text}</MarkdownContent>
          </div>
        </div>
      ) : (
        <div className="flex gap-3">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center">
            <ArgusMark size={26} className="text-blue" />
          </span>
          <div className="relative min-w-0 flex-1 text-[15px] leading-relaxed text-ink">
            <div className="mb-1 flex items-center gap-2">
              <span className="text-xs font-semibold text-blue">Argus</span>
              <CopyButton
                text={r.text}
                label={t('copy.message')}
                copiedLabel={t('copy.copied')}
                className="ml-auto opacity-60 sm:opacity-0 sm:group-hover:opacity-100"
              />
              <time className="font-mono text-[10px] tabular-nums text-ink-faint">{clockOf(ev)}{responseLatency}</time>
            </div>
            <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{r.text}</MarkdownContent>
          </div>
        </div>
      )}
    </article>
  );
}

function RoleLogGroup({
  role,
  rows,
  open,
  active,
  onToggle,
}: {
  role: typeof ROLE_ORDER[number];
  rows: ActivityRow[];
  open: boolean;
  active: boolean;
  onToggle: () => void;
}) {
  const { t } = useI18n();
  const color = theme.role[role];
  const logScroller = useRef<HTMLDivElement>(null);
  const tailLength = rows[rows.length - 1]?.r.text.length ?? 0;
  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => {
      if (logScroller.current && logScroller.current.scrollHeight > logScroller.current.clientHeight) {
        logScroller.current.scrollTop = logScroller.current.scrollHeight;
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open, rows.length, tailLength]);
  return (
    <section
      className="role-log-group border-b border-line/50"
      data-role={role}
      data-open={open ? 'true' : 'false'}
      data-active={active ? 'true' : 'false'}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="group flex h-11 w-full items-center gap-2 px-4 text-left transition-colors hover:bg-bg/60"
      >
        <span
          className={`h-2 w-2 rounded-full ${active ? 'animate-pulse' : 'opacity-55'}`}
          style={{ background: color }}
        />
        <span className="text-xs font-semibold text-ink-dim">{roleLabel(role, t)}</span>
        <span className="font-mono text-xs text-ink-faint">{rows.length}</span>
        {rows.length > 0 ? <span className="min-w-0 flex-1 truncate text-xs text-ink-faint">{rows[rows.length - 1].r.text}</span> : <span className="flex-1" />}
        <svg viewBox="0 0 16 16" aria-hidden="true" className={`h-4 w-4 shrink-0 text-ink-faint transition-transform duration-panel ease-panel ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <path d="m6 3.5 4.5 4.5L6 12.5" />
        </svg>
      </button>
      {open ? (
        <div className="grid grid-rows-[1fr]">
          <div className="min-h-0 overflow-hidden">
            <div ref={logScroller} className="max-h-72 overflow-x-hidden overflow-y-auto border-t border-line/40 scroll-thin">
              {rows.length > 0 ? rows.map(({ ev, r, key }, index) => (
                <EventRow key={key} ev={ev} r={r} first={index === 0} last={index === rows.length - 1} />
              )) : <div className="px-4 py-3 text-xs text-ink-faint">{t('stream.noLogs')}</div>}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function partitionRoleRows(rows: ActivityRow[]) {
  const roleRows: Record<typeof ROLE_ORDER[number], ActivityRow[]> = {
    manager: [],
    planner: [],
    engineer: [],
    reviewer: [],
  };
  const systemRows: ActivityRow[] = [];
  rows.forEach((row) => {
    if (ROLE_ORDER.includes(row.r.role as typeof ROLE_ORDER[number])) {
      roleRows[row.r.role as typeof ROLE_ORDER[number]].push(row);
    } else {
      systemRows.push(row);
    }
  });
  const lastRole = [...rows].reverse().find((row) =>
    ROLE_ORDER.includes(row.r.role as typeof ROLE_ORDER[number]),
  )?.r.role ?? '';
  return { roleRows, systemRows, lastRole };
}

function SystemLogGroup({ rows }: { rows: ActivityRow[] }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <section className="border-b border-line/50" data-system-open={open ? 'true' : 'false'}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="flex h-10 w-full items-center gap-2 px-4 text-left text-xs text-ink-faint hover:bg-bg/60"
      >
        <span>{t('stream.system')}</span>
        <span className="font-mono">{rows.length}</span>
        <span className="flex-1" />
        <svg viewBox="0 0 16 16" aria-hidden="true" className={`h-4 w-4 shrink-0 transition-transform duration-panel ease-panel ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <path d="m6 3.5 4.5 4.5L6 12.5" />
        </svg>
      </button>
      {open ? (
        <div className="border-t border-line/40">
          {rows.map(({ ev, r, key }, index) => (
            <EventRow key={key} ev={ev} r={r} first={index === 0} last={index === rows.length - 1} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function RoleLogCollection({ rows, live }: { rows: ActivityRow[]; live: boolean }) {
  const { roleRows, systemRows, lastRole } = useMemo(() => partitionRoleRows(rows), [rows]);
  const [openRoles, setOpenRoles] = useState<Set<string>>(
    () => new Set(live && lastRole ? [lastRole] : []),
  );
  const userToggledRole = useRef(false);
  useEffect(() => {
    if (!live || !lastRole || userToggledRole.current) return;
    setOpenRoles(new Set([lastRole]));
  }, [lastRole, live]);

  return (
    <div className="bg-bg/25">
      {ROLE_ORDER.map((role) => (
        <RoleLogGroup
          key={role}
          role={role}
          rows={roleRows[role]}
          open={openRoles.has(role)}
          active={lastRole === role}
          onToggle={() => {
            userToggledRole.current = true;
            setOpenRoles((current) => {
              const next = new Set(current);
              if (next.has(role)) next.delete(role);
              else next.add(role);
              return next;
            });
          }}
        />
      ))}
      {systemRows.length > 0 ? <SystemLogGroup rows={systemRows} /> : null}
    </div>
  );
}

export function deliveryFromEvent(event: EventMsg): DeliveryReceipt | null {
  const delivery = event.delivery;
  if (!delivery || typeof delivery !== 'object' || Array.isArray(delivery)) return null;
  const candidate = delivery as Partial<DeliveryReceipt>;
  if (typeof candidate.delivery_id !== 'string' || !candidate.delivery_id.trim()) return null;
  return candidate as DeliveryReceipt;
}

export function latestConversationDelivery(
  events: EventMsg[],
): DeliveryReceipt | null | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type === 'ui.operator') return null;
    const delivery = deliveryFromEvent(event);
    if (delivery) return delivery;
  }
  return undefined;
}

function DeliveryCard({
  delivery,
  onOpen,
}: {
  delivery: DeliveryReceipt;
  onOpen?: (delivery: DeliveryReceipt) => void;
}) {
  const { t } = useI18n();
  const certified = delivery.kind === 'submission_certified';
  return (
    <aside className="mx-auto my-3 flex w-full max-w-full gap-3 rounded-lg border border-ok/35 bg-ok/5 px-4 py-3 lg:max-w-[61.8vw]">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ok/15 font-semibold text-ok">✓</span>
      <div className="min-w-0 flex-1">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ok">
          {t(certified ? 'mission.deliveryCertified' : 'mission.taskCompleted')}
        </div>
        <div className="mt-1 truncate text-sm font-semibold text-ink" title={delivery.title}>{delivery.title}</div>
        {delivery.summary ? <p className="mt-1 text-xs leading-5 text-ink-dim">{delivery.summary}</p> : null}
        {onOpen ? (
          <button
            type="button"
            onClick={() => onOpen(delivery)}
            className="mt-2 rounded border border-ok/40 px-2 py-1 font-mono text-[10px] text-ok hover:border-ok hover:bg-ok/10"
          >
            {t(delivery.primary_target ? 'mission.openResult' : 'mission.viewTask')}
          </button>
        ) : null}
      </div>
    </aside>
  );
}

function ConversationThread({
  group,
  latest,
  artifacts,
  onOpenArtifact,
  onOpenDelivery,
}: {
  group: ConversationGroup;
  latest: boolean;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  onOpenDelivery?: (delivery: DeliveryReceipt) => void;
}) {
  const isSystemMessage = (row: ActivityRow) =>
    row.ev.type === 'ui.argus' && /^(info:|operation cancelled|cancelled\b)/i.test(row.r.text.trim());
  const replyParts = group.rows
    .filter((row) => row.ev.type === 'ui.argus')
    .map((row) => {
      const messages = row.r.text.match(RUNTIME_INFO_PATTERN) ?? [];
      const text = row.r.text.replace(RUNTIME_INFO_PATTERN, '').trim();
      return {
        reply: text && !isSystemMessage(row) ? { ...row, r: { ...row.r, text } } : null,
        messages: isSystemMessage(row) && messages.length === 0 ? [row.r.text] : messages,
      };
    });
  const replies = replyParts.flatMap((part) => part.reply ? [part.reply] : []);
  const systemMessages = replyParts.flatMap((part) => part.messages);
  const operational = group.rows.filter(({ ev }) => ev.type !== 'ui.argus');
  const deliveries = (() => {
    const seen = new Set<string>();
    return group.rows.flatMap((row) => {
      const delivery = deliveryFromEvent(row.ev);
      if (!delivery || seen.has(delivery.delivery_id)) return [];
      seen.add(delivery.delivery_id);
      return [delivery];
    });
  })();

  return (
    <section className="conversation-thread border-b border-line/60">
      <ConversationRow
        ev={group.operator.ev}
        r={group.operator.r}
        artifacts={artifacts}
        onOpenArtifact={onOpenArtifact}
      />
      {replies.map((row) => (
        <ConversationRow
          key={row.key}
          ev={row.ev}
          r={row.r}
          artifacts={artifacts}
          onOpenArtifact={onOpenArtifact}
        />
      ))}
      {systemMessages.map((message, index) => (
        <div key={`${group.key}-system-${index}`} className="mx-auto w-full max-w-full px-6 py-1.5 text-center text-xs text-ink-faint lg:max-w-[61.8vw]">
          {message}
        </div>
      ))}
      {deliveries.map((delivery) => (
        <DeliveryCard key={delivery.delivery_id} delivery={delivery} onOpen={onOpenDelivery} />
      ))}
      {operational.length > 0 ? (
        <div className="mx-auto w-full max-w-full border-t border-line/40 lg:max-w-[61.8vw]">
          <RoleLogCollection rows={operational} live={latest} />
        </div>
      ) : null}
    </section>
  );
}

/**
 * The live event feed — a CLEAN, whitelisted stream (matching the terminal
 * cockpit), not a raw event dump. Non-whitelisted events (agent_io.* framing,
 * telemetry, internal bookkeeping) are dropped; provider reasoning is opt-in
 * and visually quiet, with ⌘/Ctrl+T available to show or hide it.
 * Auto-follows the tail unless the user scrolls up to read history.
 */
export function EventStream({
  events,
  connected,
  showReasoning,
  onToggleReasoning,
  embedded = false,
  filter = 'all',
  query = '',
  skipFirst = 0,
  artifacts,
  onOpenArtifact,
  onOpenDelivery,
}: {
  events: EventMsg[];
  connected: boolean;
  showReasoning: boolean;
  onToggleReasoning: () => void;
  embedded?: boolean;
  filter?: EventViewFilter;
  query?: string;
  skipFirst?: number;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  onOpenDelivery?: (delivery: DeliveryReceipt) => void;
}) {
  const { locale, t } = useI18n();
  const [following, setFollowing] = useState(true);
  const [activityTick, setActivityTick] = useState(() => Date.now());
  const scroller = useRef<HTMLDivElement>(null);
  // Rendering a long Markdown/event history is interruptible, so incoming
  // provider fragments never take priority over typing or scrolling.
  const deferredEvents = useDeferredValue(events);
  const activeProvider = useMemo(
    () => activeProviderRequest(deferredEvents),
    [deferredEvents],
  );
  useEffect(() => {
    if (!activeProvider) return;
    setActivityTick(Date.now());
    const id = window.setInterval(() => setActivityTick(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [activeProvider]);
  const providerElapsed = activeProvider
    ? Math.max(0, Math.floor((activityTick - Number(activeProvider.ts ?? 0) * 1_000) / 1_000))
    : 0;

  // render + whitelist + COALESCE streaming message fragments once per change.
  // engineer.progress message events stream in fragments sharing a message_id
  // (replace=True); the REPL collapses them to one line — we keep the longest
  // fragment at its first position so a streaming reply is ONE growing row, not
  // a char-by-char flood.
  const baseRows = useMemo(() => {
    const out: { ev: EventMsg; r: Rendered; key: string }[] = [];
    const msgRow = new Map<string, number>(); // message_id → index in out
    let hiddenReasoning = 0;
    const displayEvents = skipFirst > 0
      ? deferredEvents.slice(skipFirst)
      : deferredEvents;
    displayEvents.forEach((ev, i) => {
      const r = renderEvent(ev, locale);
      if (!r) return; // non-whitelisted → hidden
      if (r.reasoning && !showReasoning) {
        hiddenReasoning++;
        return;
      }
      if (!eventMatchesView(ev, r, filter, query)) return;
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
        out[idx] = {
          ...out[idx],
          ev: { ...out[idx].ev, ...ev },
          r: {
            ...out[idx].r,
            ...r,
            text: mergeFragment(out[idx].r.text, r.text, fragmentMode(ev)),
          },
        };
        return;
      }
      const entry = { ev, r, key: eventKey(ev, i) };
      if (isMsg) msgRow.set(mid, out.length);
      out.push(entry);
    });
    return { list: out, hiddenReasoning };
  }, [deferredEvents, showReasoning, filter, query, skipFirst, locale]);

  const rows = baseRows;
  const conversations = useMemo(() => {
    const groups: ConversationGroup[] = [];
    const earlier: ActivityRow[] = [];
    let current: ConversationGroup | null = null;
    rows.list.forEach((row) => {
      if (row.ev.type === 'ui.operator') {
        current = { key: row.key, operator: row, rows: [] };
        groups.push(current);
      } else if (current) {
        current.rows.push(row);
      } else {
        earlier.push(row);
      }
    });
    return { groups, earlier };
  }, [rows.list]);

  const reasoningTotal = useMemo(
    () => deferredEvents.filter(isReasoning).length,
    [deferredEvents],
  );
  const tailContentLength = useMemo(
    () => rows.list.slice(-20).reduce((total, row) => total + row.r.text.length, 0),
    [rows.list],
  );

  useEffect(() => {
    if (!following) return;
    const frame = window.requestAnimationFrame(() => {
      if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [rows.list.length, tailContentLength, following]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const jump = () => {
    setFollowing(true);
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  };

  return (
    <section className={`relative flex min-h-0 flex-1 flex-col overflow-hidden bg-panel ${
      embedded ? '' : 'rounded-lg border border-line/80'
    }`}>
      <PanelHeader
        title={t('panel.activity')}
        right={
          <div className="flex items-center gap-3">
            <button
              onClick={onToggleReasoning}
              className={`rounded px-1.5 py-0.5 text-xs transition-colors ${
                showReasoning ? 'text-blue-sky' : 'text-ink-faint hover:text-ink-dim'
              }`}
              title={t('stream.toggleReasoning')}
            >
              {t('stream.reasoning')}{reasoningTotal ? ` ·${reasoningTotal}` : ''}
            </button>
            <span className={`text-xs ${connected ? 'text-ok' : 'text-ink-faint'}`}>
              {connected ? `● ${t('common.live')}` : `○ ${t('common.reconnecting')}`}
            </span>
          </div>
        }
      />
      {activeProvider ? (
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-line/60 bg-blue-deep/5 px-4 text-xs text-ink-dim">
          <span className="h-2 w-2 animate-pulse rounded-full bg-blue-sky" />
          <span className="truncate">
            {t('stream.backgroundWork')}
          </span>
          <span className="ml-auto shrink-0 font-mono tabular-nums text-ink-faint">
            {providerElapsed}s
          </span>
        </div>
      ) : null}
      <div ref={scroller} className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto pb-6 pt-1.5 scroll-thin">
        {rows.list.length === 0 ? (
          <EmptyHint>{t('stream.ready')}</EmptyHint>
        ) : (
          <>
            {conversations.earlier.length > 0 ? (
              <section className="mx-auto w-full max-w-full border-b border-line/60 lg:max-w-[61.8vw]">
                <div className="flex h-10 items-center gap-2 border-b border-line/40 px-4 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-faint">
                  {t('stream.autonomous')}
                  <span className="font-mono font-normal tracking-normal">{conversations.earlier.length}</span>
                </div>
                <RoleLogCollection
                  rows={conversations.earlier}
                  live={conversations.groups.length === 0}
                />
              </section>
            ) : null}
            {conversations.groups.map((group, index) => (
              <ConversationThread
                key={group.key}
                group={group}
                latest={index === conversations.groups.length - 1}
                artifacts={artifacts}
                onOpenArtifact={onOpenArtifact}
                onOpenDelivery={onOpenDelivery}
              />
            ))}
          </>
        )}
      </div>
      {!following && (
        <button
          onClick={jump}
          aria-label={t('stream.jumpToLatest')}
          title={t('stream.jumpToLatest')}
          className="absolute bottom-4 left-1/2 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-line/60 bg-panel text-sm text-ink-dim shadow-glow transition-all duration-200 hover:border-ink-faint hover:text-ink"
        >
          ↓
        </button>
      )}
    </section>
  );
}
