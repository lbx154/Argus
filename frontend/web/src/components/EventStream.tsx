import { cleanDeliverySummary } from './deliveryPresentation';
import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { useGsapMotion } from '../lib/motion';
import type { ArtifactInfo, EventMsg } from '../api';
import type { DeliveryReceipt } from '../../../core/src/types';
import type { RenderedLine } from '../../../core/src/eventRender';
import { isReasoning, type EventViewFilter } from '../../../core/src/events';
import {
  foldFeedRows,
  groupSummary,
  renderFeedRows,
  rowPreview,
  type FeedGroup,
  type FeedRow,
  type FeedRowInput,
  type StepStatus,
} from '../lib/feedSteps';
import { theme, toneColor } from '../lib/theme';
import { clockOf } from '../lib/format';
import { PanelHeader, EmptyHint } from './primitives';
import { MarkdownContent } from './MarkdownContent';
import { ArgusMark } from './Wordmark';
import { useI18n } from '../i18n';
import { CopyButton } from './CopyButton';
import { roleLabel } from '../lib/enumLabels';
import { TurnSteps } from './TurnSteps';
import { turnStepsFrom } from '../../../core/src/phaseTrail';
import { plainDetail } from '../lib/plainStatus';

type ActivityRow = { ev: EventMsg; r: RenderedLine; key: string };
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

function EventRow({
  ev,
  r,
  first,
  last,
  latest,
  repeat = 1,
  status = '',
  result,
}: {
  ev: EventMsg;
  r: RenderedLine;
  first: boolean;
  last: boolean;
  /** The newest event behind this row — its clock is the one shown. */
  latest?: EventMsg;
  /** How many identical rows this one stands for. */
  repeat?: number;
  status?: StepStatus;
  result?: FeedRowInput;
}) {
  const { locale, t } = useI18n();
  const roleHue = theme.role[r.role] ?? theme.inkFaint;
  const color = toneColor(r.tone);
  const plain = plainDetail(r.text, locale);
  const resultText = result ? plainDetail(result.r.text, locale).text : '';
  const tooltip = [plain.technical, resultText ? `${t('stream.stepResult')}: ${resultText}` : ''].filter(Boolean).join('\n');
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
          {repeat > 1 ? (
            <span
              className="rounded bg-line/60 px-1 font-mono text-[10px] tabular-nums text-ink-dim"
              title={t('stream.repeated', { count: repeat })}
              data-repeat={repeat}
            >
              ×{repeat}
            </span>
          ) : null}
          {status === 'failed' ? <span className="text-xs text-err">{t('stream.stepFailed')}</span> : null}
          <time className="ml-auto font-mono text-xs tabular-nums text-ink-faint opacity-0 transition-opacity group-hover:opacity-100">
            {clockOf(latest ?? ev)}
          </time>
        </div>
        <div className={`mt-0.5 whitespace-pre-wrap break-words text-sm leading-5 ${r.reasoning ? 'italic' : ''}`} style={{ color }} title={tooltip || undefined}>
          {plain.text}
        </div>
      </div>
    </div>
  );
}

/** Consecutive calls of one tool as one line — "Read 5 files: …" — that opens to its steps. */
function FeedGroupRow({
  group,
  first,
  last,
  open,
  onToggle,
}: {
  group: FeedGroup;
  first: boolean;
  last: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const { locale, t } = useI18n();
  const roleHue = theme.role[group.r.role] ?? theme.inkFaint;
  const color = toneColor(group.r.tone);
  const summary = groupSummary(group, t, locale);
  return (
    <div
      className={`event-activity-row group relative grid grid-cols-[16px_minmax(0,1fr)] gap-3 px-4 py-3 transition-colors hover:bg-bg/70 ${last ? 'animate-appear' : ''}`}
      data-feed-group={group.action}
      data-open={open ? 'true' : 'false'}
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
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          title={t(open ? 'stream.fold.collapse' : 'stream.fold.expand')}
          className="block w-full rounded text-left focus-visible:outline focus-visible:outline-1 focus-visible:outline-blue-sky"
        >
          <div className="flex items-center gap-2">
            <span
              className="truncate text-xs font-semibold uppercase tracking-[0.06em]"
              style={{ color: roleHue }}
              title={group.r.label}
            >
              {group.r.label}
            </span>
            <span className="text-xs" style={{ color }}>{group.r.glyph}</span>
            <span className="font-mono text-[10px] tabular-nums text-ink-faint">{group.calls}</span>
            <svg viewBox="0 0 16 16" aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 text-ink-faint transition-transform duration-panel ease-panel ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="m6 3.5 4.5 4.5L6 12.5" />
            </svg>
            <time className="ml-auto font-mono text-xs tabular-nums text-ink-faint opacity-0 transition-opacity group-hover:opacity-100">
              {clockOf(group.latest)}
            </time>
          </div>
          <div className="mt-0.5 whitespace-pre-wrap break-words text-sm leading-5" style={{ color }}>
            {summary}
          </div>
        </button>
        {open ? (
          <div className="mt-1 border-l border-line/50">
            {group.steps.map((step, index) => (
              <EventRow
                key={step.key}
                ev={step.ev}
                r={step.r}
                first={index === 0}
                last={index === group.steps.length - 1}
                latest={step.latest}
                repeat={step.repeat}
                status={step.status}
                result={step.result}
              />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** The folded rows of one list, with each group's open state kept while the feed grows. */
function FeedRows({ rows }: { rows: FeedRow[] }) {
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set());
  const toggle = (key: string) => setOpenGroups((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });
  return (
    <>
      {rows.map((row, index) => row.kind === 'group' ? (
        <FeedGroupRow
          key={row.key}
          group={row}
          first={index === 0}
          last={index === rows.length - 1}
          open={openGroups.has(row.key)}
          onToggle={() => toggle(row.key)}
        />
      ) : (
        <EventRow
          key={row.key}
          ev={row.ev}
          r={row.r}
          first={index === 0}
          last={index === rows.length - 1}
          latest={row.latest}
          repeat={row.repeat}
          status={row.status}
          result={row.result}
        />
      ))}
    </>
  );
}

function ConversationRow({
  ev,
  r,
  artifacts,
  onOpenArtifact,
}: {
  ev: EventMsg;
  r: RenderedLine;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
}) {
  const { t } = useI18n();
  const operator = String(ev.type) === 'ui.operator';
  const steps = operator ? [] : turnStepsFrom(ev.steps);
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
            <ArgusMark size={26} className="text-ink" />
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
            {steps.length ? <TurnSteps steps={steps} live={ev.live === true} /> : null}
            {r.text ? <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{r.text}</MarkdownContent> : null}
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
  const { t, locale } = useI18n();
  const color = theme.role[role];
  const logScroller = useRef<HTMLDivElement>(null);
  const tailLength = rows[rows.length - 1]?.r.text.length ?? 0;
  const folded = useMemo(() => foldFeedRows(rows, locale), [rows, locale]);
  const preview = folded.length ? rowPreview(folded[folded.length - 1], t, locale) : '';
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
          data-role-dot={role}
          aria-hidden="true"
          className={`h-2 w-2 shrink-0 rounded-full ${active ? 'animate-pulse motion-reduce:animate-none' : ''}`}
          style={{ background: color }}
        />
        <span className="text-xs font-semibold text-ink-dim">{roleLabel(role, t)}</span>
        <span className="font-mono text-xs text-ink-faint">{folded.length}</span>
        {preview ? <span className="min-w-0 flex-1 truncate text-xs text-ink-faint">{preview}</span> : <span className="flex-1" />}
        <svg viewBox="0 0 16 16" aria-hidden="true" className={`h-4 w-4 shrink-0 text-ink-faint transition-transform duration-panel ease-panel ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <path d="m6 3.5 4.5 4.5L6 12.5" />
        </svg>
      </button>
      {open ? (
        <div className="grid grid-rows-[1fr]">
          <div className="min-h-0 overflow-hidden">
            <div ref={logScroller} className="max-h-72 overflow-x-hidden overflow-y-auto border-t border-line/40 scroll-thin">
              {folded.length > 0 ? <FeedRows rows={folded} /> : <div className="px-4 py-3 text-xs text-ink-faint">{t('stream.noLogs')}</div>}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function partitionRoleRows(rows: ActivityRow[]) {
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
  const { t, locale } = useI18n();
  const [open, setOpen] = useState(false);
  const folded = useMemo(() => foldFeedRows(rows, locale), [rows, locale]);
  return (
    <section className="border-b border-line/50" data-system-open={open ? 'true' : 'false'}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="flex h-10 w-full items-center gap-2 px-4 text-left text-xs text-ink-faint hover:bg-bg/60"
      >
        <span>{t('stream.system')}</span>
        <span className="font-mono">{folded.length}</span>
        <span className="flex-1" />
        <svg viewBox="0 0 16 16" aria-hidden="true" className={`h-4 w-4 shrink-0 transition-transform duration-panel ease-panel ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <path d="m6 3.5 4.5 4.5L6 12.5" />
        </svg>
      </button>
      {open ? (
        <div className="border-t border-line/40">
          <FeedRows rows={folded} />
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
        {delivery.summary ? <p className="mt-1 text-xs leading-5 text-ink-dim">{cleanDeliverySummary(delivery.summary)}</p> : null}
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
      const working = Array.isArray(row.ev.steps) && row.ev.steps.length > 0;
      return {
        reply: (text || working) && !isSystemMessage(row) ? { ...row, r: { ...row.r, text } } : null,
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
  showHeader = true,
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
  showHeader?: boolean;
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

  // render + whitelist + COALESCE streaming message fragments once per change
  // (see renderFeedRows). Folding into steps happens per displayed list, in the
  // role and system groups, where "consecutive" means what the reader sees.
  const baseRows = useMemo(() => {
    const displayEvents = skipFirst > 0
      ? deferredEvents.slice(skipFirst)
      : deferredEvents;
    return renderFeedRows(displayEvents, { locale, showReasoning, filter, query });
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
      {showHeader && <PanelHeader
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
      />}
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
