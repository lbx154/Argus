import { Fragment, createContext, useContext, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { useGsapMotion } from '../lib/motion';
import { applicationBrand } from '../applicationBrand';
import type { ArtifactInfo, EventMsg, Snapshot } from '../api';
import type { DeliveryReceipt, MissionView } from '../../../core/src/types';
import { type RenderedLine } from '../../../core/src/eventRender';
import { isDraftReply, type EventViewFilter } from '../../../core/src/events';
import {
  foldFeedRows,
  groupSummary,
  plainCommand,
  plainToolLabel,
  renderFeedRows,
  type FeedGroup,
  type FeedRow,
  type FeedRowInput,
  type StepStatus,
} from '../lib/feedSteps';
import { theme, toneColor } from '../lib/theme';
import { clockOf, dateOf } from '../lib/format';
import { durableMessageId } from '../lib/conversationEvents';
import { EmptyHint, RawDisclosure } from './primitives';
import { MarkdownContent, WorkspaceImage } from './MarkdownContent';
import { deliveryFiles } from './deliveryPresentation';

const IMAGE_FILE = /\.(png|jpe?g|gif|webp|svg)$/i;
import { ModelThumbnail } from './ModelPreview';
import { modelFormat } from '../lib/modelFiles';
import { MarkdownExcerpt } from './MarkdownExcerpt';
import { useI18n, type Locale } from '../i18n';
import { deliveryHeadingKey, isStagePackage } from '../lib/deliveryKind';
import { CopyButton } from './CopyButton';
import { roleLabel } from '../lib/enumLabels';
import { AGENT_ROLES as ROLE_ORDER } from '../lib/agentRoles';
import { TurnSteps } from './TurnSteps';
import { turnStepsFrom } from '../../../core/src/phaseTrail';
import { plainDetail, plainTaskReport } from '../lib/plainStatus';
import { isCallRow, plainRowText, roleHeadline, type ProgressText } from '../lib/plainProgress';
import { activeProviderRequest, currentWorkStatus, eventTaskId } from '../lib/workStatus';
import { ReplyOptionButtons, readableQuestions, splitReplyOptions } from './ReplyOptions';
import { StepProcessContext, StepProcessDisclosure } from './LiveWorkCard';
import { earlierRoleWork } from './agentActivityModel';
import { liveActivity, liveLines, type LiveLine } from '../lib/liveActivity';
export { activeProviderRequest } from '../lib/workStatus';
import { splitDraft } from '../map/presentation';
import type { ConversationHistoryStatus } from '../useConversationHistory';
import { ShowcasePrompts, StartPrompts } from './StartPrompts';
import { writeLocalStorage } from '../lib/storage';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';
import { resumableTaskIds } from '../lib/resumable';

/** The project's workspace roots, so replies show paths relative to them. */
export const WorkspaceRootsContext = createContext<string[]>([]);
import type { ConversationRevision } from './ExperiencePanels';
import { History, PencilLine, Play } from 'lucide-react';

type ActivityRow = { ev: EventMsg; r: RenderedLine; key: string };
// A thread that project work interrupted continues without its operator row.
type ConversationGroup = { key: string; operator?: ActivityRow; rows: ActivityRow[] };
// One card per task, where its work started; '' collects work that names no task.
type TaskBlock = { key: string; taskId: string; rows: ActivityRow[]; deliveries: DeliveryReceipt[] };
export type TimelineBlock = ({ kind: 'thread' } & ConversationGroup) | ({ kind: 'task' } & TaskBlock);
const RUNTIME_INFO_PATTERN = /Info: (?:Operation cancelled by user|Response was interrupted due to a server error\. Retrying\.\.\.)/gi;

/** The project root the agents work in; a command's path in it reads from there. */
const WorkspaceContext = createContext('');
const ConversationActionContext = createContext<((target: ConversationRevision) => void) | undefined>(undefined);
/** The project whose files the conversation shows, for inline images. */
export const SessionIdContext = createContext<string | null>(null);
/** A task's title by its id, so a round names the task it belongs to. */
const TaskTitleContext = createContext<(id: string) => string>(() => '');
/** Run, offered on a task's newest receipt while that task waits to be resumed. */
const ResumeTaskContext = createContext<(taskId: string, messageId: string) => (() => void) | undefined>(() => undefined);

type RowText = ProgressText & { prose: boolean };

/** A row as the feed says it: a call reads as "verb + object" or as the command
 * it ran, with its raw text kept for the tooltip; a report in plain words, and
 * in its Markdown when a role wrote it, as the conversation shows it. */
function rowText(ev: EventMsg, r: RenderedLine, locale: Locale, workspace: string, taskTitle?: (id: string) => string): RowText {
  const call = ev.type === 'engineer.progress' ? String(ev.kind || '') : '';
  if (call === 'tool_use') return { text: plainToolLabel(ev, locale), technical: r.text, prose: false };
  if (call === 'command_execution') return { text: plainCommand(r.text, workspace), technical: r.text, prose: false };
  return { ...plainRowText(ev, r, locale, taskTitle), prose: !['tool_result', 'file_change'].includes(call) };
}

/** The task a record belongs to, by its title, else its id: one role can work on several at once. */
function TaskTag({ ev }: { ev: EventMsg }) {
  const id = eventTaskId(ev);
  const title = useContext(TaskTitleContext)(id);
  return id ? <span data-row-task={id} className="min-w-0 truncate text-[11px] text-ink-faint" title={title || id}>{title || id}</span> : null;
}

function RowWords({ text, prose }: Pick<RowText, 'text' | 'prose'>) {
  return prose ? <MarkdownExcerpt>{text}</MarkdownExcerpt> : <>{text}</>;
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
  const workspace = useContext(WorkspaceContext);
  const taskTitle = useContext(TaskTitleContext);
  const plain = rowText(ev, r, locale, workspace, taskTitle);
  const [fullOpen, setFullOpen] = useState(false);
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
          <TaskTag ev={ev} />
          {r.glyph ? <span className="text-xs" style={{ color }}>{r.glyph}</span> : null}
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
          <RowWords {...plain} />
        </div>
        {plain.full ? <>
          <button type="button" data-full-toggle aria-expanded={fullOpen} onClick={() => setFullOpen(current => !current)}
            className="mt-1 rounded text-xs text-ink-faint hover:text-ink-dim">
            {t(fullOpen ? 'stream.hideFull' : 'stream.showFull')}
          </button>
          {fullOpen ? <div data-full-text className="mt-1 whitespace-pre-wrap break-words border-l border-line/60 pl-3 text-xs leading-5 text-ink-dim">
            <RowWords text={plain.full} prose />
          </div> : null}
        </> : null}
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
            <TaskTag ev={group.latest} />
            {group.r.glyph ? <span className="text-xs" style={{ color }}>{group.r.glyph}</span> : null}
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
  revision,
}: {
  ev: EventMsg;
  r: RenderedLine;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  revision?: ConversationRevision;
}) {
  const { t, locale } = useI18n();
  const sessionId = useContext(SessionIdContext);
  const roots = useContext(WorkspaceRootsContext);
  const shownPaths = (value: string) => relativizeWorkspacePaths(value, ...roots);
  const onAction = useContext(ConversationActionContext);
  const resumeFor = useContext(ResumeTaskContext);
  const operator = String(ev.type) === 'ui.operator';
  const taskReceipt = !operator && ev.mission_result === true;
  const resume = taskReceipt && typeof ev.item_id === 'string'
    ? resumeFor(ev.item_id, String(ev.message_id || ev.event_id || '')) : undefined;
  // Preserve the full recorded message, including research/control-like lines.
  const reportText = taskReceipt ? (typeof ev.text === 'string' ? ev.text : r.text) : r.text;
  const report = taskReceipt ? plainTaskReport(reportText, locale) : null;
  const recordedAt = taskReceipt ? dateOf(ev) : null;
  const draft = operator ? splitDraft(r.text) : null;
  const references = draft?.refs ?? [];
  const text = draft && references.length ? draft.text : r.text;
  // A question's numbered options become buttons; typing a free answer still works.
  const asked = !operator && !taskReceipt ? splitReplyOptions(reportText) : null;
  const steps = operator ? [] : turnStepsFrom(ev.steps);
  const responseLatencyMs = Number(ev.response_latency_ms ?? 0);
  const responseLatency = !operator && responseLatencyMs >= 100
    ? ` · ${(responseLatencyMs / 1_000).toFixed(1)}s`
    : '';
  const revisionLabel = locale === 'zh-CN' ? '编辑并重发' : 'Edit and resend';
  const versionsLabel = locale === 'zh-CN' ? '版本记录' : 'Versions';
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
    <article ref={rowRef} data-conversation-ts={ev.ts} data-task-receipt={taskReceipt || undefined}
      data-task-receipt-id={taskReceipt ? String(ev.message_id || ev.event_id || '') : undefined}
      className="conversation-row group mx-auto w-full max-w-full px-4 py-4 sm:px-6 lg:max-w-[808px]">
      {operator ? (
        <div className="flex items-end justify-end gap-2">
          <div className="conversation-message">
            {references.length ? <div className="mb-2 space-y-2">
              {references.map((reference, index) => <blockquote
                key={`${reference.source}:${reference.task_id}:${reference.step_id}:${index}`}
                aria-label={locale === 'zh-CN' ? '引用' : 'Reference'}
                className="border-l-2 border-blue/40 pl-3 text-sm text-ink-dim"
              >
                <div className="text-xs text-ink-faint">{locale === 'zh-CN' ? '引用' : 'Reference'}{reference.part ? ` · ${locale === 'zh-CN' ? `第 ${reference.part} 部分` : `Part ${reference.part}`}` : ''}</div>
                <div className="font-medium">{reference.task_title || reference.step_title || reference.task_id}</div>
                {reference.task_title && reference.step_title && reference.step_title !== reference.task_title
                  ? <div>{reference.step_title}</div> : null}
              </blockquote>)}
            </div> : null}
            {text ? <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{text}</MarkdownContent> : null}
          </div>
        </div>
      ) : (
        <div className="argus-reply">
          <div className="relative min-w-0 flex-1 text-[15px] leading-[1.7] text-ink">
            {/* Offset the feed's pt-1.5 so scrolled text cannot peek above a pinned receipt header. */}
            <div
              data-task-receipt-header={taskReceipt || undefined}
              className={taskReceipt
                ? 'sticky -top-1.5 z-10 mb-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 bg-panel py-1'
                : 'mb-1 flex items-center gap-2'}
            >
              <span className="argus-reply-name">{applicationBrand().name}</span>
              {taskReceipt ? <span className="text-[11px] text-ink-faint">
                {locale === 'zh-CN' ? '任务回报 · 当时记录' : 'Task report · Recorded then'}
              </span> : null}
              {taskReceipt && <><CopyButton
                text={reportText}
                label={report ? (locale === 'zh-CN' ? '复制原始回报' : 'Copy original report') : t('copy.message')}
                copiedLabel={t('copy.copied')}
                className="ml-auto opacity-60 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
              />
              <time
                className={`font-mono text-[10px] tabular-nums text-ink-faint ${taskReceipt ? 'basis-full sm:basis-auto' : ''}`}
                dateTime={recordedAt?.toISOString()}
                title={recordedAt?.toLocaleString(locale, { dateStyle: 'full', timeStyle: 'long' })}
              >{recordedAt ? recordedAt.toLocaleString(locale, {
                year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
              }) : clockOf(ev)}{responseLatency}</time></>}
            </div>
            {reportText ? <div data-task-receipt-prose={taskReceipt || undefined}><MarkdownContent sid={sessionId} artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{shownPaths(report?.text ?? (asked ? readableQuestions(asked.body) : reportText))}</MarkdownContent>{asked?.options.length ? <ReplyOptionButtons options={asked.options} zh={locale === 'zh-CN'} /> : null}</div> : null}
            {resume ? <button type="button" data-receipt-resume onClick={resume}
              className="brand-button brand-button-primary mt-3 inline-flex items-center gap-1.5">
              <Play size={14} aria-hidden="true" />{t('topbar.continueRun')}
            </button> : null}
            {steps.length || ev.live === true ? <TurnSteps steps={steps} live={ev.live === true} startedTs={Number(ev.ts)} replied={Boolean(reportText) && !isDraftReply(ev)}
              status={ev.success === false ? 'failed' : String(ev.status ?? '')} /> : null}
            {taskReceipt && typeof ev.item_id === 'string' && ev.item_id ? <StepProcessDisclosure taskId={ev.item_id} /> : null}
            {report ? <RawDisclosure className="task-receipt-original" label={locale === 'zh-CN' ? '查看原始回报（含技术记录）' : 'View original report (including technical records)'}>
              <pre data-task-receipt-original className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-bg p-3 font-mono text-xs text-ink-dim">{reportText}</pre>
            </RawDisclosure> : null}
          </div>
        </div>
      )}
      {!taskReceipt && ev.live !== true && <div className={`conversation-meta ${operator ? 'justify-end' : ''}`}>
        <time>{clockOf(ev)}{responseLatency}</time>
        <div className="conversation-tools">
        <CopyButton text={r.text} label={t('copy.message')} copiedLabel={t('copy.copied')} className="conversation-copy" />
        {revision && onAction && (operator
          ? <button type="button" aria-label={revisionLabel} title={revisionLabel} onClick={() => onAction({ ...revision, mode: 'edit' })}><PencilLine size={14} /><span>{revisionLabel}</span></button>
          : <button type="button" aria-label={versionsLabel} title={versionsLabel} onClick={() => onAction({ ...revision, mode: 'versions' })}><History size={14} /><span>{versionsLabel}</span></button>)}
        </div>
      </div>}
    </article>
  );
}

function RoleLogGroup({
  role,
  rows,
  open,
  active,
  tasks,
  earlier,
  onToggle,
}: {
  role: typeof ROLE_ORDER[number];
  rows: ActivityRow[];
  open: boolean;
  active: boolean;
  /** The tasks it is at work on now. */
  tasks: string[];
  /** What the role last said in the whole record, for when its loaded log says nothing. */
  earlier?: string;
  onToggle: () => void;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const logScroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const savedScrollTop = useRef(0);
  const [readingHistory, setReadingHistory] = useState(false);
  const tailLength = rows[rows.length - 1]?.r.text.length ?? 0;
  // The commands a role ran are its process: folded until the reader asks.
  const [showCommands, setShowCommands] = useState(false);
  const commandCount = useMemo(() => rows.filter(row => isCallRow(row.ev)).length, [rows]);
  const shown = useMemo(() => showCommands ? rows : rows.filter(row => !isCallRow(row.ev)), [rows, showCommands]);
  const folded = useMemo(() => foldFeedRows(shown, locale), [shown, locale]);
  // The collapsed header says what the role is doing or concluded, never a command.
  const taskTitle = useContext(TaskTitleContext);
  const headline = useMemo(() => roleHeadline(rows, locale, taskTitle) || earlier || '', [rows, locale, taskTitle, earlier]);
  // At work on several tasks at once, it says what it does now on each.
  const several = active && tasks.length > 1;
  const each = useMemo(() => several ? tasks.map(id => ({
    id, title: taskTitle(id) || id, now: roleHeadline(rows.filter(row => eventTaskId(row.ev) === id), locale, taskTitle),
  })) : [], [several, tasks, rows, locale, taskTitle]);
  useEffect(() => {
    if (open && !following.current && logScroller.current) {
      logScroller.current.scrollTop = savedScrollTop.current;
    }
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => {
      // Read the ref in the frame too: the user may scroll up after a new
      // event scheduled this follow, before the browser paints it.
      if (following.current && logScroller.current) {
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
          className={`h-2 w-2 shrink-0 rounded-full ${active ? 'bg-ok animate-pulse motion-reduce:animate-none' : 'bg-ink-faint/50'}`}
        />
        <span className={`text-xs font-semibold ${active ? 'text-ok' : 'text-ink-faint'}`}>{roleLabel(role, t)}</span>
        <span data-role-headline className="min-w-0 flex-1 truncate text-xs text-ink-faint" title={headline.replace(/\*\*|`/g, '') || undefined}>
          {several ? (zh ? `· ${tasks.length} 个任务进行中` : `· ${tasks.length} tasks under way`)
            : headline
            ? <>{active ? (zh ? '现在：' : 'Now: ') : (zh ? '之前：' : 'Last: ')}<RowWords text={headline} prose /></>
            : rows.length ? null : zh ? '还没开始' : 'Not started'}
        </span>
        <svg viewBox="0 0 16 16" aria-hidden="true" className={`h-4 w-4 shrink-0 text-ink-faint transition-transform duration-panel ease-panel ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <path d="m6 3.5 4.5 4.5L6 12.5" />
        </svg>
      </button>
      {each.length ? <ul data-role-tasks className="-mt-1 space-y-0.5 px-4 pb-2 pl-8 text-xs text-ink-faint">
        {each.map(task => <li key={task.id} data-role-task={task.id} className="truncate">
          <span className="text-ink-dim">{task.title}</span>{' · '}{zh ? '现在：' : 'Now: '}{task.now ? <RowWords text={task.now} prose /> : zh ? '刚开始' : 'Just started'}
        </li>)}
      </ul> : null}
      {open ? (
        <div className="grid grid-rows-[1fr]">
          {commandCount > 0 ? <div className="flex border-t border-line/40 px-4 py-1.5">
            <button type="button" data-command-toggle aria-expanded={showCommands} onClick={() => setShowCommands(current => !current)}
              className="rounded text-xs text-ink-faint hover:text-ink-dim">
              {showCommands ? t('stream.hideCommands') : t('stream.showCommands', { count: commandCount })}
            </button>
          </div> : null}
          <div className="relative min-h-0 overflow-hidden">
            <div ref={logScroller}
              onScroll={(event) => {
                const element = event.currentTarget;
                savedScrollTop.current = element.scrollTop;
                following.current = element.scrollHeight - element.scrollTop - element.clientHeight <= 2;
                setReadingHistory(!following.current);
              }}
              className="max-h-72 overflow-x-hidden overflow-y-auto border-t border-line/40 scroll-thin">
              {folded.length > 0 ? <FeedRows rows={folded} /> : commandCount > 0 ? null : <div className="px-4 py-3 text-xs text-ink-faint">{t('stream.noLogs')}</div>}
            </div>
            {readingHistory ? <button
              type="button"
              onClick={() => {
                following.current = true;
                setReadingHistory(false);
                if (logScroller.current) logScroller.current.scrollTop = logScroller.current.scrollHeight;
              }}
              className="absolute bottom-2 right-3 rounded-full border border-line/60 bg-panel px-3 py-1 text-xs text-ink-dim shadow-glow hover:text-ink"
            >↓ {t('stream.jumpToLatest')}</button> : null}
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

function RoleLogCollection({ rows, active, tasks, earlier, followLatest }: {
  rows: ActivityRow[];
  /** Whether the role is at work now. */
  active: (role: string) => boolean;
  /** The tasks the role is at work on now. */
  tasks: (role: string) => string[];
  /** What the role last said in the whole record. */
  earlier?: (role: string) => string | undefined;
  followLatest: boolean;
}) {
  const { roleRows, systemRows, lastRole } = useMemo(() => partitionRoleRows(rows), [rows]);
  const [openRoles, setOpenRoles] = useState<Set<string>>(
    () => new Set(followLatest && lastRole ? [lastRole] : []),
  );
  const userToggledRole = useRef(false);
  useEffect(() => {
    if (!followLatest || !lastRole || userToggledRole.current) return;
    setOpenRoles(new Set([lastRole]));
  }, [lastRole, followLatest]);

  return (
    <div className="bg-bg/25">
      {ROLE_ORDER.map((role) => (
        <RoleLogGroup
          key={role}
          role={role}
          rows={roleRows[role]}
          open={openRoles.has(role)}
          active={active(role)}
          tasks={tasks(role)}
          earlier={earlier?.(role)}
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

function fileGlyph(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? '';
  if (['csv', 'tsv', 'xlsx', 'xls', 'parquet'].includes(ext)) return '▦';
  if (modelFormat(path)) return '◇';
  if (ext === 'pdf') return '◧';
  if (['py', 'ts', 'tsx', 'js', 'sh', 'r', 'ipynb', 'json', 'yaml', 'yml'].includes(ext)) return '‹›';
  return '▤';
}

/**
 * A delivered result in the conversation, shown as itself: a picture shows the
 * picture, files show as files. "Open" leads to the result; the process behind
 * it is the team's progress, in the conversation.
 */
function DeliveryCard({
  delivery,
  onOpen,
}: {
  delivery: DeliveryReceipt;
  onOpen?: (delivery: DeliveryReceipt, path?: string) => void;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const sid = useContext(SessionIdContext);
  const stage = isStagePackage(delivery);
  const files = deliveryFiles(delivery);
  // Result first: a delivered robot, model, chart or picture shows itself, not
  // just a button. A model wins over a picture: it is the thing itself, and
  // the picture is usually a render of it. Without a project to load it from,
  // it stays in the file list.
  const visual = sid ? files.find((file) => modelFormat(file.path)) ?? files.find((file) => IMAGE_FILE.test(file.path)) : undefined;
  const others = files.filter(file => file !== visual);
  const listed = others.slice(0, 3);
  return (
    <aside
      data-delivery-kind={delivery.kind}
      data-stage={stage || undefined}
      className="result-card mx-auto my-4 w-[calc(100%-2rem)] max-w-full sm:w-[calc(100%-3rem)] lg:max-w-[760px]"
    >
      {visual && sid && modelFormat(visual.path) ? <div className="result-card-preview result-card-model" data-delivery-model={visual.path}>
        <ModelThumbnail sid={sid} path={visual.path} label={visual.label}
          onOpen={onOpen ? () => onOpen(delivery, visual.path) : undefined} />
      </div> : visual && sid ? <div className="result-card-preview" data-delivery-thumbnail={visual.path}>
        <WorkspaceImage sid={sid} path={visual.path} src={visual.path} alt={visual.label || visual.path}
          className="max-h-64 w-auto max-w-full object-contain"
          onOpen={onOpen ? () => onOpen(delivery, visual.path) : undefined} />
        <span className="sr-only">{zh ? '点击查看大图' : 'Click to view full size'}</span>
      </div> : null}
      <div className="result-card-body">
        <div className="result-card-kind">
          <span className="status-dot" data-tone={stage ? 'running' : 'done'} aria-hidden="true" />
          {t(deliveryHeadingKey(delivery))}
        </div>
        <div className="result-card-title" title={delivery.title}>{delivery.title}</div>
        {listed.length ? <ul className="result-card-files">
          {listed.map(file => <li key={file.path} title={file.path}>
            <span aria-hidden="true">{fileGlyph(file.path)}</span>{file.label || file.path.split('/').pop()}
          </li>)}
          {others.length > listed.length ? <li className="text-ink-faint">{zh ? `另有 ${others.length - listed.length} 个文件` : `${others.length - listed.length} more files`}</li> : null}
        </ul> : null}
        {onOpen ? <div className="result-card-actions">
          <button type="button" onClick={() => onOpen(delivery)} className="result-card-open"
            aria-label={t(delivery.primary_target ? 'mission.openResult' : 'mission.viewTask')}>
            {zh ? '打开' : 'Open'}
          </button>
        </div> : null}
      </div>
    </aside>
  );
}

/** A run of three or more "step done, campaign continues" receipts keeps only
 * its newest message open; the earlier ones fold behind one control. */
export function foldedContinuationRuns(rows: Array<{ key: string; ev: EventMsg }>): Map<string, { id: string; keys: string[] }> {
  const folded = new Map<string, { id: string; keys: string[] }>();
  let run: string[] = [];
  const close = () => {
    if (run.length >= 3) {
      const keys = run.slice(0, -1);
      const entry = { id: keys[0], keys };
      keys.forEach((key) => folded.set(key, entry));
    }
    run = [];
  };
  for (const row of rows) {
    // Older transcripts did not keep the flag; their message id still names the kind.
    const continued = row.ev.campaign_continues === true || /^mission-result-.+-continued$/.test(String(row.ev.message_id ?? ''));
    if (row.ev.mission_result === true && continued) run.push(row.key);
    else close();
  }
  close();
  return folded;
}

/**
 * The conversation view as one stream in time order, newest last: dialogue
 * threads, and one compact card per task placed where its work started, so a
 * message sent while work runs lands at the bottom, not under a work log.
 * Work stays out of the threads (task A's work is no answer to question B).
 * A chat reply stays directly under the message it answers, so a task that
 * started while it was being written follows it; a task report is that work's
 * outcome, so the card comes first and the thread continues after it.
 */
export function conversationTimeline(rows: ActivityRow[]): TimelineBlock[] {
  const blocks: TimelineBlock[] = [];
  const tasks = new Map<string, Extract<TimelineBlock, { kind: 'task' }>>();
  let latestTask = '';
  let held: ActivityRow[] = [];
  let awaitingReply = false;
  const addWork = (row: ActivityRow) => {
    // A note that names no task belongs to the task in hand.
    const taskId = eventTaskId(row.ev) || latestTask;
    // A Manager reply's own steps already show on that reply.
    if (taskId.startsWith('turn:')) return;
    let task = tasks.get(taskId);
    if (!task) {
      task = { kind: 'task', key: `task-${row.key}`, taskId, rows: [], deliveries: [] };
      tasks.set(taskId, task);
      blocks.push(task);
    }
    task.rows.push(row);
    if (taskId) latestTask = taskId;
  };
  const release = () => {
    held.forEach(addWork);
    held = [];
  };
  rows.forEach((row) => {
    if (row.ev.type === 'ui.operator') {
      release();
      blocks.push({ kind: 'thread', key: row.key, operator: row, rows: [] });
      awaitingReply = true;
    } else if (row.ev.type === 'ui.argus') {
      if (row.ev.mission_result === true || deliveryFromEvent(row.ev)) release();
      const last = blocks.at(-1);
      if (last?.kind === 'thread') last.rows.push(row);
      else blocks.push({ kind: 'thread', key: `thread-${row.key}`, rows: [row] });
      awaitingReply = false;
      release();
    } else if (awaitingReply) {
      held.push(row);
    } else {
      addWork(row);
    }
  });
  release();
  // A receipt a reply already shows is not repeated under the work.
  const seen = new Set(rows.filter(row => row.ev.type === 'ui.argus')
    .map(row => deliveryFromEvent(row.ev)?.delivery_id).filter(Boolean));
  blocks.forEach((block) => {
    if (block.kind !== 'task') return;
    block.deliveries = block.rows.flatMap((row) => {
      const delivery = deliveryFromEvent(row.ev);
      if (!delivery || seen.has(delivery.delivery_id)) return [];
      seen.add(delivery.delivery_id);
      return [delivery];
    });
  });
  return blocks;
}


type TaskCardState = 'running' | 'done' | 'failed' | 'skipped' | 'paused' | 'idle';

const TASK_STATE_LABEL: Record<Exclude<TaskCardState, 'running' | 'idle'>, [string, string]> = {
  done: ['已完成', 'Done'],
  failed: ['没有完成', 'Not finished'],
  skipped: ['已跳过', 'Skipped'],
  paused: ['已暂停', 'Paused'],
};

/**
 * A task in the conversation: one line on where it stands, and its results.
 * What its roles said is the team's progress below, said there once.
 */
function TaskWorkCard({ block, state, title, snapshot, view, events, onOpenDelivery }: {
  block: Extract<TimelineBlock, { kind: 'task' }>;
  state: TaskCardState;
  title: string;
  snapshot?: Snapshot;
  view?: MissionView | null;
  events: EventMsg[];
  onOpenDelivery?: (delivery: DeliveryReceipt, path?: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [now, setNow] = useState(() => Date.now() / 1_000);
  const running = state === 'running';
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1_000), 5_000);
    return () => window.clearInterval(timer);
  }, [running]);
  const activity = useMemo(() => running && snapshot ? liveActivity(snapshot, view, events, now, locale) : null,
    [events, locale, now, running, snapshot, view]);
  const label = state === 'running' || state === 'idle' ? '' : TASK_STATE_LABEL[state][zh ? 0 : 1];
  return (
    <section data-task-work={block.taskId || 'project'} data-state={state}
      className="task-line mx-auto w-full max-w-full px-4 py-1 sm:px-6 lg:max-w-[808px]">
      <div className="task-line-row text-[13px]">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="status-dot" data-tone={running ? 'running' : state === 'done' ? 'done' : state === 'failed' ? 'failed' : 'idle'} aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate" role={running ? 'status' : undefined}>
            <span className="text-ink-dim">{running
              ? activity?.headline || (zh ? `正在进行：${title}` : `Working on: ${title}`)
              : label ? `${label}：${title}` : title}</span>
          </span>
        </div>
        {activity?.note ? <p className="mt-1 pl-[17px] text-xs text-ink-faint">
          {activity.note}
        </p> : null}
      </div>
      {block.deliveries.map(delivery => <DeliveryCard key={delivery.delivery_id} delivery={delivery} onOpen={onOpenDelivery} />)}
    </section>
  );
}

/** Insert the mission view's older role work at its place in time. */
function withEarlierWork(earlier: EventMsg[], events: EventMsg[]): EventMsg[] {
  if (!earlier.length) return events;
  const merged: EventMsg[] = [];
  let next = 0;
  events.forEach((event) => {
    const ts = Number(event.ts);
    while (next < earlier.length && Number(earlier[next].ts) < ts) merged.push(earlier[next++]);
    merged.push(event);
  });
  return [...merged, ...earlier.slice(next)];
}

function ConversationThread({
  group,
  artifacts,
  onOpenArtifact,
  onOpenDelivery,
}: {
  group: ConversationGroup;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  onOpenDelivery?: (delivery: DeliveryReceipt, path?: string) => void;
}) {
  const isSystemMessage = (row: ActivityRow) =>
    row.ev.type === 'ui.argus' && /^(info:|operation cancelled|cancelled\b)/i.test(row.r.text.trim());
  const argusRows = group.rows.filter((row) => row.ev.type === 'ui.argus');
  // Editions are stored by message id, so only a transcript id can key them.
  const source = group.operator && durableMessageId(group.operator.ev);
  const revision: ConversationRevision | undefined = group.operator && source ? {
    source,
    prompt: group.operator.r.text,
    answer: argusRows.filter(row => row.ev.mission_result !== true).map(row => row.r.text).join('\n\n'),
    mode: 'edit', baseVersion: 0,
  } : undefined;
  const replyParts = argusRows
    .map((row) => {
      // Historical reports keep their complete event text for the original view
      // and copy action, including whitespace and any embedded runtime notice.
      if (row.ev.mission_result === true) return { key: row.key, reply: row, messages: [] };
      const messages = row.r.text.match(RUNTIME_INFO_PATTERN) ?? [];
      const text = row.r.text.replace(RUNTIME_INFO_PATTERN, '').trim();
      const working = row.ev.live === true || (Array.isArray(row.ev.steps) && row.ev.steps.length > 0);
      return {
        key: row.key,
        reply: (text || working) && !isSystemMessage(row) ? { ...row, r: { ...row.r, text } } : null,
        messages: isSystemMessage(row) && messages.length === 0 ? [row.r.text] : messages,
      };
    });
  const replies = replyParts.flatMap((part) => part.reply ? [part.reply] : []);
  const folded = foldedContinuationRuns(replies);
  const [openRuns, setOpenRuns] = useState<Set<string>>(() => new Set());
  const { locale } = useI18n();
  // Notices and receipts follow the reply that carried them, in time order.
  const seenDeliveries = new Set<string>();
  const after = new Map(replyParts.map((part, index) => {
    const delivery = deliveryFromEvent(argusRows[index].ev);
    const fresh = delivery && !seenDeliveries.has(delivery.delivery_id) ? delivery : null;
    if (fresh) seenDeliveries.add(fresh.delivery_id);
    return [part.key, { messages: part.messages, delivery: fresh }];
  }));
  const trailing = (key: string) => {
    const extra = after.get(key);
    if (!extra) return null;
    return <>
      {extra.messages.map((message, index) => (
        <div key={`${key}-system-${index}`} className="mx-auto w-full max-w-full px-6 py-1.5 text-center text-xs text-ink-faint lg:max-w-[808px]">
          {message}
        </div>
      ))}
      {extra.delivery ? <DeliveryCard delivery={extra.delivery} onOpen={onOpenDelivery} /> : null}
    </>;
  };

  return (
    <section className="conversation-thread">
      {group.operator ? <ConversationRow
        ev={group.operator.ev}
        r={group.operator.r}
        artifacts={artifacts}
        onOpenArtifact={onOpenArtifact}
        revision={revision}
      /> : null}
      {replyParts.map(({ key, reply: row }) => {
        if (!row) return <Fragment key={key}>{trailing(key)}</Fragment>;
        const run = folded.get(row.key);
        if (run && !openRuns.has(run.id)) {
          return <Fragment key={key}>
            {run.keys[0] === row.key ? (
              <button type="button" data-folded-steps={run.keys.length}
                onClick={() => setOpenRuns((current) => new Set(current).add(run.id))}
                className="mx-auto my-1 block w-full max-w-full px-4 py-1.5 text-center text-xs text-ink-faint hover:text-ink-dim lg:max-w-[808px]">
                {locale === 'zh-CN' ? `较早的 ${run.keys.length} 步进展已折叠 · 展开` : `${run.keys.length} earlier step updates folded · Show`}
              </button>
            ) : null}
            {trailing(key)}
          </Fragment>;
        }
        return (
          <Fragment key={key}>
            <ConversationRow
              ev={row.ev}
              r={row.r}
              artifacts={artifacts}
              onOpenArtifact={onOpenArtifact}
              revision={revision}
            />
            {trailing(key)}
          </Fragment>
        );
      })}
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
  embedded = false,
  showHeader = true,
  filter = 'all',
  query = '',
  skipFirst = 0,
  artifacts,
  onOpenArtifact,
  onOpenDelivery,
  snapshot,
  missionView,
  historyStatus = 'ready',
  historyRefreshing = false,
  refreshingKept = false,
  onRetryHistory,
  onCompose,
  rememberPosition = false,
  onConversationAction,
  requestedMessageTs,
  onResumeWork,
}: {
  events: EventMsg[];
  connected: boolean;
  showReasoning: boolean;
  embedded?: boolean;
  showHeader?: boolean;
  filter?: EventViewFilter;
  query?: string;
  skipFirst?: number;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  onOpenDelivery?: (delivery: DeliveryReceipt, path?: string) => void;
  snapshot?: Snapshot;
  missionView?: MissionView | null;
  historyStatus?: ConversationHistoryStatus;
  historyRefreshing?: boolean;
  /** Showing content kept from an earlier visit while it refreshes. */
  refreshingKept?: boolean;
  /** Offered on a running task's card when it looks stuck. */
  onRetryHistory?: () => void;
  onCompose?: (text: string) => void;
  rememberPosition?: boolean;
  onConversationAction?: (target: ConversationRevision) => void;
  requestedMessageTs?: number;
  /** Run again; offered on the receipt of a task that waits to be resumed. */
  onResumeWork?: () => void;
}) {
  const { locale, t } = useI18n();
  const [following, setFollowing] = useState(true);
  const followingRef = useRef(true);
  const lastScrollTop = useRef(0);
  const [activityTick, setActivityTick] = useState(() => Date.now());
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const searchSeen = useRef<number | undefined>();
  const [readingError, setReadingError] = useState(false);
  // Rendering a long Markdown/event history is interruptible, so incoming
  // provider fragments never take priority over typing or scrolling.
  const deferredEvents = useDeferredValue(events);
  useEffect(() => {
    if (requestedMessageTs === undefined) { searchSeen.current = undefined; return; }
    if (searchSeen.current === requestedMessageTs) return;
    const target = content.current?.querySelector<HTMLElement>(`[data-conversation-ts="${requestedMessageTs}"]`);
    if (target) {
      content.current?.querySelectorAll<HTMLElement>('[data-search-hit]').forEach(previous => { delete previous.dataset.searchHit; });
      target.dataset.searchHit = 'true';
      target.scrollIntoView({ block: 'center' });
      searchSeen.current = requestedMessageTs;
      setFollowing(false);
      followingRef.current = false;
    }
  }, [requestedMessageTs, deferredEvents]);
  const workspace = snapshot?.session.workdir || snapshot?.session.cwd || '';
  const taskTitle = useMemo(() => {
    const titles = new Map((snapshot?.backlog ?? []).map((item) => [item.id, item.title || '']));
    return (id: string) => titles.get(id) ?? '';
  }, [snapshot?.backlog]);
  // A finished step keeps its process: the actions between its task's start
  // and end, computed only when a report asks for them.
  const stepProcess = useMemo(() => {
    const cache = new Map<string, LiveLine[]>();
    const tasks = new Map((snapshot?.backlog ?? []).map((item) => [item.id, item]));
    return (taskId: string) => {
      const cached = cache.get(taskId);
      if (cached) return cached;
      const task = tasks.get(taskId);
      const since = Number(task?.started_ts || 0);
      const lines = since > 0 ? liveLines(deferredEvents, taskId, since, locale, Number(task?.finished_ts || 0) || Infinity, workspace) : [];
      cache.set(taskId, lines);
      return lines;
    };
  }, [deferredEvents, locale, snapshot?.backlog, workspace]);
  const work = useMemo(
    () => snapshot ? currentWorkStatus(snapshot, missionView, deferredEvents) : null,
    [deferredEvents, missionView, snapshot],
  );
  const live = connected && (!snapshot || work?.state === 'running');
  const activeProvider = useMemo(
    () => snapshot || !connected ? null : activeProviderRequest(deferredEvents),
    [connected, deferredEvents, snapshot],
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
  const deferredView = useDeferredValue(missionView);
  const baseRows = useMemo(() => {
    const displayEvents = skipFirst > 0
      ? deferredEvents.slice(skipFirst)
      : withEarlierWork(earlierRoleWork(deferredView, deferredEvents, locale), deferredEvents);
    return renderFeedRows(displayEvents, { locale, showReasoning, filter, query, workspace });
  }, [deferredEvents, deferredView, showReasoning, filter, query, skipFirst, locale, workspace]);

  const rows = baseRows;
  const timeline = useMemo(() => {
    const blocks = conversationTimeline(rows.list);
    const tasks = blocks.filter((block) => block.kind === 'task');
    const latest = [...tasks].sort((left, right) => Number(left.rows.at(-1)?.ev.ts ?? 0) - Number(right.rows.at(-1)?.ev.ts ?? 0)).at(-1);
    const workRows = rows.list.filter((row) => row.ev.type !== 'ui.operator' && row.ev.type !== 'ui.argus');
    return { blocks, latestTask: latest?.key, workRows };
  }, [rows.list]);
  // Who works now, as the server knows it: several roles can at once, and none while the run is down.
  const working = useMemo(() => new Set(connected && snapshot?.daemon.alive ? snapshot.roles.filter(role => role.active).map(role => role.role) : []),
    [connected, snapshot?.daemon.alive, snapshot?.roles]);
  const resumable = useMemo(() => resumableTaskIds(snapshot), [snapshot]);
  const resumeFor = useMemo(() => {
    // Only a task's newest receipt offers Run; earlier ones are history.
    const newest = new Map<string, string>();
    for (const event of deferredEvents) {
      if (event.type === 'ui.argus' && event.mission_result === true && typeof event.item_id === 'string') {
        newest.set(event.item_id, String(event.message_id || event.event_id || ''));
      }
    }
    return (taskId: string, messageId: string) => onResumeWork && resumable.has(taskId) && newest.get(taskId) === messageId
      ? onResumeWork : undefined;
  }, [deferredEvents, onResumeWork, resumable]);
  const taskState = (block: Extract<TimelineBlock, { kind: 'task' }>): TaskCardState => {
    const item = snapshot?.backlog?.find((candidate) => candidate.id === block.taskId);
    const completed = [...block.rows].reverse().find(row => row.ev.type === 'life.mission.completed');
    const status = String(item?.status || completed?.ev.status || '');
    if (status === 'done') return 'done';
    if (status === 'failed' || status === 'blocked') return 'failed';
    if (status === 'skipped') return 'skipped';
    if (live && (work?.taskId ? work.taskId === block.taskId : block.key === timeline.latestTask)) return 'running';
    return status === 'running' || status === 'pending' ? 'paused' : 'idle';
  };
  const blockTitle = (block: Extract<TimelineBlock, { kind: 'task' }>) => {
    const item = snapshot?.backlog?.find((candidate) => candidate.id === block.taskId);
    const named = item?.title || block.rows.map(row => row.ev.mission_title || row.ev.task_title).find(Boolean);
    return String(named || (block.taskId
      ? locale === 'zh-CN' ? '这项工作' : 'This work'
      : locale === 'zh-CN' ? '项目工作' : 'Project work'));
  };

  const tailContentLength = useMemo(
    () => rows.list.slice(-20).reduce((total, row) => total + row.r.text.length, 0),
    [rows.list],
  );

  useEffect(() => {
    if (!following) return;
    const frame = window.requestAnimationFrame(() => {
      if (followingRef.current && scroller.current) {
        scroller.current.scrollTop = scroller.current.scrollHeight;
        lastScrollTop.current = scroller.current.scrollTop;
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [rows.list.length, tailContentLength, following]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const positionKey = rememberPosition && snapshot ? `argus.conversation.position.v1:${snapshot.session.id}` : null;
    let restored = false;
    const restorePosition = () => {
      if (restored || !positionKey || historyStatus !== 'ready' || el.clientHeight === 0) return;
      restored = true;
      try {
        const raw = localStorage.getItem(positionKey);
        if (!raw) return;
        const saved: unknown = JSON.parse(raw);
        if (!saved || typeof saved !== 'object' || !('top' in saved) || typeof saved.top !== 'number'
          || !Number.isFinite(saved.top) || saved.top < 0 || !('following' in saved) || typeof saved.following !== 'boolean') throw new Error('Invalid reading position');
        followingRef.current = saved.following;
        setFollowing(saved.following);
        if (!saved.following) {
          el.scrollTop = saved.top;
          lastScrollTop.current = el.scrollTop;
        }
      } catch { setReadingError(true); }
    };
    restorePosition();
    const onScroll = () => {
      if (el.clientHeight === 0) return;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      // Scroll anchoring can move down during layout growth before the resize
      // callback catches up. Only an upward movement leaves an active follow;
      // reaching the bottom resumes it, including after a viewport clamp.
      followingRef.current = atBottom || (followingRef.current && el.scrollTop >= lastScrollTop.current - 1);
      lastScrollTop.current = el.scrollTop;
      setFollowing(followingRef.current);
      if (positionKey && restored) setReadingError(!writeLocalStorage(positionKey, JSON.stringify({ top: el.scrollTop, following: followingRef.current })));
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    let frame: number | null = null;
    const observer = new ResizeObserver(() => {
      restorePosition();
      if (!followingRef.current) return;
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        frame = null;
        // Reflow can move the bottom without adding an event. A reader who
        // scrolled into history while this frame was queued still takes priority.
        if (followingRef.current) {
          el.scrollTop = el.scrollHeight;
          lastScrollTop.current = el.scrollTop;
        }
      });
    });
    observer.observe(el);
    if (content.current) observer.observe(content.current);
    return () => {
      el.removeEventListener('scroll', onScroll);
      observer.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [snapshot?.session.id, historyStatus, rememberPosition]);

  const jump = () => {
    followingRef.current = true;
    setFollowing(true);
    if (scroller.current) {
      scroller.current.scrollTo({ top: scroller.current.scrollHeight, behavior: 'auto' });
      lastScrollTop.current = scroller.current.scrollTop;
    }
  };

  return (
    <ConversationActionContext.Provider value={onConversationAction}>
    <StepProcessContext.Provider value={stepProcess}>
    <WorkspaceContext.Provider value={workspace}>
    <TaskTitleContext.Provider value={taskTitle}>
    <ResumeTaskContext.Provider value={resumeFor}>
    <section className={`relative flex min-h-0 flex-1 flex-col overflow-hidden bg-panel ${
      embedded ? '' : 'rounded-lg border border-line/80'
    }`}>
      {showHeader && (!connected || refreshingKept) ? <div className="chat-context">
        <h2 className="sr-only">{t('panel.activity')}</h2>
        <div className="chat-context-tools">
          {refreshingKept ? <span role="status" data-refreshing-kept>
            {t('stream.refreshingKept')}
          </span> : connected ? <span className="sr-only">{t('common.live')}</span> : <span className="chat-context-offline">
            {`○ ${t('common.reconnecting')}`}
          </span>}
        </div>
      </div> : null}
      {!snapshot && activeProvider ? (
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
      {historyStatus !== 'ready' ? <div className="flex shrink-0 items-center gap-3 px-4 py-3 text-sm text-ink-faint"
          role={historyStatus === 'error' ? 'alert' : 'status'} data-conversation-history={historyStatus}>
          <span>{t(historyStatus === 'error' ? 'stream.historyError' : 'stream.historyLoading')}</span>
          {historyStatus === 'error' && onRetryHistory ? <button type="button" onClick={onRetryHistory}
            disabled={historyRefreshing} className="shrink-0 rounded px-2 py-1 text-blue-sky hover:bg-bg disabled:opacity-50">
            {t('stream.historyRetry')}
          </button> : null}
      </div> : null}
      {readingError && <p role="status" className="px-4 py-1 text-xs text-warn">{locale === 'zh-CN' ? '浏览器无法恢复或保存阅读位置。' : 'This browser could not restore or save the reading position.'}</p>}
      <div ref={scroller} className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto pb-6 pt-1.5 scroll-thin" aria-busy={historyStatus === 'loading' || refreshingKept}>
        <div ref={content} className="flow-root" data-event-stream-content>
        {rows.list.length === 0 ? (
          historyStatus === 'ready' ? onCompose ? <div className="mx-auto max-w-2xl px-5 pb-8 pt-[12vh]">
            <h2 className="font-serif text-[28px] font-medium leading-tight tracking-[-0.01em] text-ink">{locale === 'zh-CN' ? '从你想完成的事开始' : 'Start with what you want to accomplish'}</h2>
            <p className="mt-2 text-sm leading-6 text-ink-faint">{locale === 'zh-CN' ? '直接说出目标，也可以选择下面的场景，再补充你的材料和要求。' : 'Describe your goal, or choose a starting point and add your material and requirements.'}</p>
            <StartPrompts onPick={onCompose} />
            <p className="mt-6 text-xs text-ink-faint">{locale === 'zh-CN' ? '或者直接试一个实际跑过的任务' : 'Or try a task that was run end to end'}</p>
            <ShowcasePrompts onPick={onCompose} />
          </div> : <EmptyHint>{t('stream.ready')}</EmptyHint> : null
        ) : (
          <>
            {timeline.blocks.map((block) => {
              if (block.kind === 'thread') return <ConversationThread
                key={block.key}
                group={block}
                artifacts={artifacts}
                onOpenArtifact={onOpenArtifact}
                onOpenDelivery={onOpenDelivery}
              />;
              return <TaskWorkCard key={block.key} block={block} state={taskState(block)} title={blockTitle(block)}
                snapshot={snapshot} view={missionView} events={deferredEvents}
                onOpenDelivery={onOpenDelivery} />;
            })}
            {/* The team's progress: each role, what it does now or did last, its work a click away. */}
            {timeline.workRows.length || snapshot?.roles.some(role => role.last) ? <section data-project-work aria-label={locale === 'zh-CN' ? '团队进展' : 'Team progress'}
              className="mx-auto w-full max-w-full px-4 py-2 sm:px-6 lg:max-w-[808px]">
              <div className="overflow-hidden rounded-lg border border-line/70">
                <RoleLogCollection rows={timeline.workRows} active={role => working.has(role)} followLatest
                  tasks={role => snapshot?.roles.find(row => row.role === role)?.tasks ?? []}
                  earlier={role => snapshot?.roles.find(row => row.role === role)?.last?.text} />
              </div>
            </section> : null}
          </>
        )}
        </div>
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
    </ResumeTaskContext.Provider>
    </TaskTitleContext.Provider>
    </WorkspaceContext.Provider>
    </StepProcessContext.Provider>
    </ConversationActionContext.Provider>
  );
}
