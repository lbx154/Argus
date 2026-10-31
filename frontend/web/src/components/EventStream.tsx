import { questionSentence, managerDoing, thoughtHeading, roleActionLabel } from '../lib/taskLanguage';
import { useRoleLog } from '../useRoleLog';
import { TaskDetailModal } from './TaskDetailModal';
import { TaskLetters } from './TaskLetters';
import { useBacklogItem } from '../hooks';
import { splitMessageAttachments } from '../lib/attachments';
import { ComposerAttachmentChip } from './ComposerAttachmentChip';
import type { UploadedAttachment } from '../api';
import { createContext, memo, useCallback, useContext, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { conversationEventKey } from '../lib/conversationEvents';
import { applicationBrand } from '../applicationBrand';
import type { ArtifactInfo, EventMsg, Snapshot } from '../api';
import type { DeliveryReceipt } from '../../../core/src/types';
import { type RenderedLine } from '../../../core/src/eventRender';
import { isReasoning } from '../../../core/src/events';
import { useElapsed } from '../map/LiveLine';
import { elapsedLabel } from '../map/alive';
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
import { theme } from '../lib/theme';
import { clockOf, dateOf } from '../lib/format';
import { EmptyHint } from './primitives';
import { MarkdownContent, WorkspaceImage } from './MarkdownContent';
import { deliveryFileUpdated, deliveryTimeLabel, deliveryFiles } from './deliveryPresentation';

const IMAGE_FILE = /\.(png|jpe?g|gif|webp|svg)$/i;
import { ModelThumbnail } from './ModelPreview';
import { modelFormat } from '../lib/modelFiles';
import { MarkdownExcerpt } from './MarkdownExcerpt';
import { useI18n, type Locale } from '../i18n';
import { deliveryHeadingKey, isStagePackage } from '../lib/deliveryKind';
import { CopyButton } from './CopyButton';
import { roleLabel } from '../lib/enumLabels';
import { AGENT_ROLES as ROLE_ORDER } from '../lib/agentRoles';
import { plainDetail } from '../lib/plainStatus';
import { isCallRow, plainRowText, type ProgressText } from '../lib/plainProgress';
import { activeProviderRequest, eventTaskId } from '../lib/workStatus';
import { ReplyOptionButtons, readableQuestions, splitReplyOptions } from './ReplyOptions';
export { activeProviderRequest } from '../lib/workStatus';
import { splitDraft } from '../map/presentation';
import type { ConversationHistoryStatus, ConversationPagination } from '../useConversationHistory';
import { ShowcasePrompts, StartPrompts } from './StartPrompts';
import { writeLocalStorage } from '../lib/storage';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';
import { ErrorDetails, ErrorMessage } from './ErrorMessage';

/** The project's workspace roots, so replies show paths relative to them. */
export const WorkspaceRootsContext = createContext<string[]>([]);
import { PencilLine } from 'lucide-react';

type ActivityRow = { ev: EventMsg; r: RenderedLine; key: string };
/** The project root the agents work in; a command's path in it reads from there. */
const WorkspaceContext = createContext('');
const ConversationActionContext = createContext<((prompt: string) => void) | undefined>(undefined);
/** The project whose files the conversation shows, for inline images. */
export const SessionIdContext = createContext<string | null>(null);
/** A task's title by its id, so a round names the task it belongs to. */
const TaskTitleContext = createContext<(id: string) => string>(() => '');

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
          style={{ backgroundColor: roleHue }}
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
        {/* The role's colour marks its dot and name; what it said reads in the text's own colour. */}
        <div className={`mt-0.5 whitespace-pre-wrap break-words text-sm leading-5 ${r.tone === 'dim' ? 'text-ink-dim' : 'text-ink'} ${r.reasoning ? 'italic' : ''}`} title={tooltip || undefined}>
          <RowWords {...plain} />
        </div>
        {ev.type === 'life.status' && ev.operator_alert === true ? <ErrorDetails error={ev.text} /> : null}
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
          style={{ backgroundColor: roleHue }}
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
            <span className="font-mono text-[10px] tabular-nums text-ink-faint">{group.calls}</span>
            <svg viewBox="0 0 16 16" aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 text-ink-faint transition-transform duration-panel ease-panel ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="m6 3.5 4.5 4.5L6 12.5" />
            </svg>
            <time className="ml-auto font-mono text-xs tabular-nums text-ink-faint opacity-0 transition-opacity group-hover:opacity-100">
              {clockOf(group.latest)}
            </time>
          </div>
          <div className={`mt-0.5 whitespace-pre-wrap break-words text-sm leading-5 ${group.r.tone === 'dim' ? 'text-ink-dim' : 'text-ink'}`}>
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

/** How long a reply has been under way since it was asked for, as codex's status line counts it:
 * the clock ticks on its own, so the reply's words stay still. */
function Underway({ since, zh }: { since: number; zh: boolean }) {
  return <span className="shrink-0 tabular-nums text-ink-faint" data-underway>· {elapsedLabel(useElapsed(since) ?? 0, zh)}</span>;
}

function conversationTime(ev: EventMsg) {
  const date = dateOf(ev);
  const today = new Date();
  const clock = clockOf(ev);
  if (!date || date.toDateString() === today.toDateString()) return clock;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock}`;
}

const ConversationRow = memo(function ConversationRow({
  ev,
  fallbackText,
  artifacts,
  onOpenArtifact,
  rewritePrompt,
  doing,
}: {
  ev: EventMsg;
  fallbackText: string;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  rewritePrompt?: string;
  /** The Manager's call under way: what it is at. */
  doing?: string;
}) {
  const { t, locale } = useI18n();
  const sessionId = useContext(SessionIdContext);
  const roots = useContext(WorkspaceRootsContext);
  const shownPaths = (value: string) => relativizeWorkspacePaths(value, ...roots);
  const onAction = useContext(ConversationActionContext);
  const operator = String(ev.type) === 'ui.operator';
  const message = operator ? splitMessageAttachments(typeof ev.text === 'string' ? ev.text : fallbackText, ev.attachments as UploadedAttachment[] | undefined) : null;
  const reportText = message?.text ?? (typeof ev.text === 'string' ? ev.text : fallbackText);
  const draft = operator ? splitDraft(reportText) : null;
  const references = draft?.refs ?? [];
  const text = draft && references.length ? draft.text : reportText;
  // A question's numbered options become buttons; typing a free answer still works.
  const steps = !operator && Array.isArray(ev.steps) ? ev.steps as string[] : [];
  const to = String(ev.to ?? '');
  const said = (words: string, delivery?: string) => {
    const question = splitReplyOptions(words);
    return <><MarkdownContent delivery={delivery} sid={sessionId} artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{shownPaths(question ? readableQuestions(question.body) : words)}</MarkdownContent>
      {question?.options.length ? <ReplyOptionButtons options={question.options} zh={locale === 'zh-CN'} /> : null}</>;
  };
  const answer = reportText ? <div data-manager-answer>{said(reportText, deliveryFromEvent(ev)?.delivery_id)}</div> : null;
  const replaced = <p className="text-xs text-ink-faint" data-manager-reply="replaced">{locale === 'zh-CN' ? '你之后的消息取代了它' : 'Your later message took its place.'}</p>;
  const rewriteLabel = locale === 'zh-CN' ? '编辑后重发' : 'Edit and resend';
  if (!operator && ev.status === 'replaced' && !steps.length) return (
    <article tabIndex={-1} data-operator={operator} data-conversation-ts={ev.ts} className="conversation-row mx-auto w-full max-w-full px-4 py-2 sm:px-6 lg:max-w-[808px]">
      {replaced}
    </article>
  );
  return (
    <article tabIndex={-1} data-operator={operator} data-conversation-ts={ev.ts}
      className="conversation-row group mx-auto w-full max-w-full px-4 py-4 sm:px-6 lg:max-w-[808px]">
      {operator ? (
        <div className="flex items-end justify-end gap-2">
          <div className="conversation-message">
            {message?.attachments.length ? <div className="mb-2 space-y-2" aria-label={locale === 'zh-CN' ? '附件' : 'Attachments'}>
              {message.attachments.map(file => <ComposerAttachmentChip key={file.attachment_id} uploaded={file} sent onOpen={onOpenArtifact ? () => onOpenArtifact(file.relative_path) : undefined} />)}
            </div> : null}
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
            {text ? <div data-operator-text className="whitespace-pre-wrap break-words">{text}</div> : null}
          </div>
        </div>
      ) : (
        <div className="argus-reply">
          <div className="relative min-w-0 flex-1 text-[15px] leading-[1.7] text-ink">
            <div className="mb-1 flex items-center gap-2"><span className="argus-reply-name">{applicationBrand().name}</span></div>
            {steps.length ? <div data-manager-progress className="mb-4 space-y-2 border-b border-line/50 pb-3 text-ink-dim [&_:is(strong,code,h1,h2,h3,th)]:text-inherit">
              {steps.map((text, index) => <div key={index}>{said(text)}</div>)}
            </div> : null}
            {ev.status === 'running' ? <div data-manager-reply="running">
              <div className="flex min-w-0 items-center gap-2 text-sm text-ink-dim">
                <span className="status-dot" data-tone="running" aria-hidden="true" />
                <span className="min-w-0 truncate" data-manager-doing>{doing || (locale === 'zh-CN' ? '正在回复你的消息' : 'Replying to your message')}</span>
                {Number(ev.ts) ? <Underway since={Number(ev.ts)} zh={locale === 'zh-CN'} /> : null}
              </div>
              {/* The answer as it is written; recorded steps stay above it. */}
              {answer}
            </div> : ev.status === 'replaced' ? replaced
              : ev.status === 'waiting' ? <p className="text-sm text-ink-dim" data-manager-reply="waiting">{locale === 'zh-CN' ? '等待处理' : 'Waiting to be processed'}</p>
              : ev.status === 'stopped' ? <p className="text-sm text-ink-dim" data-manager-reply="stopped">{locale === 'zh-CN' ? '已停止，点继续运行会接着回答' : 'Stopped. Click Resume to continue the reply.'}</p>
              : ev.status === 'failed' ? <p className="whitespace-pre-wrap break-words text-sm text-err">{locale === 'zh-CN' ? '回复中断：' : 'Reply interrupted: '}{reportText}</p>
              : answer && (to === 'planner' || to === 'engineer') ? <details className="conversation-brief py-1.5" data-testid="conversation-brief">
                <summary className="cursor-pointer text-sm text-ink-dim hover:text-ink">
                  {locale === 'zh-CN' ? `已交给${roleLabel(to, t)}` : `Handed to the ${roleLabel(to, t)}`}
                </summary><div className="mt-1.5 border-l-2 border-line pl-3">{answer}</div>
              </details> : answer}
          </div>
        </div>
      )}
      {ev.live !== true && <div className={`conversation-meta ${operator ? 'justify-end' : ''}`}>
        <time>{conversationTime(ev)}</time>
        <div className="conversation-tools">
        {reportText ? <CopyButton text={reportText} label={t('copy.message')} copiedLabel={t('copy.copied')} className="conversation-copy" /> : null}
        {/* The operator edits the message in the composer before sending it again. */}
        {rewritePrompt && onAction && operator && <button type="button" aria-label={rewriteLabel} title={rewriteLabel} onClick={() => onAction(rewritePrompt)}><PencilLine size={14} /><span>{rewriteLabel}</span></button>}
        </div>
      </div>}
    </article>
  );
});

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
  const zh = locale === 'zh-CN';
  const [visibleCount, setVisibleCount] = useState(5);
  // The commands a role ran are its process: folded until the reader asks.
  const [showCommands, setShowCommands] = useState(false);
  const commandCount = useMemo(() => rows.filter(row => isCallRow(row.ev)).length, [rows]);
  const shown = useMemo(() => showCommands ? rows : rows.filter(row => !isCallRow(row.ev)), [rows, showCommands]);
  const folded = useMemo(() => foldFeedRows(shown, locale), [shown, locale]);
  const visible = folded.slice(-visibleCount);
  // Folded, a role still says what it is thinking, as its backend sums it up, and since when: what
  // it said is its task's progress, and what it ran stays folded.
  const thought = useMemo(() => [...rows].reverse().find(row => isReasoning(row.ev)), [rows]);
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
        <span data-role-dot={role} aria-hidden="true" className="status-dot" data-tone={active ? 'running' : undefined} />
        <span className={`text-xs font-semibold ${active ? 'text-ink' : 'text-ink-faint'}`}>{roleLabel(role, t)}</span>
        <svg viewBox="0 0 16 16" aria-hidden="true" className={`h-4 w-4 shrink-0 text-ink-faint transition-transform duration-panel ease-panel ${open ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <path d="m6 3.5 4.5 4.5L6 12.5" />
        </svg>
        {!open && thought ? <>
          <span data-role-thought className="min-w-0 flex-1 truncate text-xs italic text-ink-dim">{thoughtHeading(String(thought.ev.text ?? ''), zh) || roleActionLabel(role, zh)}</span>
          <time className="shrink-0 font-mono text-[11px] tabular-nums text-ink-faint">{conversationTime(thought.ev)}</time>
        </> : null}
      </button>
      {open ? (
        <div className="grid grid-rows-[1fr]">
          {commandCount > 0 ? <div className="flex border-t border-line/40 px-4 py-1.5">
            <button type="button" data-command-toggle aria-expanded={showCommands} onClick={() => setShowCommands(current => !current)}
              className="rounded text-xs text-ink-faint hover:text-ink-dim">
              {showCommands ? t('stream.hideCommands') : t('stream.showCommands')}
            </button>
          </div> : null}
          <div className="border-t border-line/40" data-role-log>
            {folded.length > visibleCount ? <button type="button" data-role-earlier onClick={() => setVisibleCount(count => count + 5)}
              className="w-full px-4 py-2 text-left text-xs text-ink-faint hover:text-ink-dim">{zh ? '显示更早' : 'Show earlier'}</button> : null}
            {visible.length > 0 ? <FeedRows rows={visible} /> : commandCount > 0 ? null : <div className="px-4 py-3 text-xs text-ink-faint">{t('stream.noLogs')}</div>}
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
  return { roleRows, systemRows };
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

function RoleLogCollection({ rows, active }: {
  rows: ActivityRow[];
  /** Whether the role is at work now. */
  active: (role: string) => boolean;
}) {
  const { roleRows, systemRows } = useMemo(() => partitionRoleRows(rows), [rows]);
  const [openRoles, setOpenRoles] = useState<Set<string>>(() => new Set());

  return (
    <div className="bg-bg/25">
      {/* A role that has taken no part has nothing to say here. */}
      {ROLE_ORDER.filter(role => roleRows[role].length || active(role)).map((role) => (
        <RoleLogGroup
          key={role}
          role={role}
          rows={roleRows[role]}
          open={openRoles.has(role)}
          active={active(role)}
          onToggle={() => {
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

/** Both surfaces read the rows stream's project cache and the server's work status. */
export function ProjectRoleLogs({ snapshot, connected, details = false, taskId }: {
  snapshot: Snapshot; connected: boolean; details?: boolean; taskId?: string;
}) {
  const { locale } = useI18n();
  const log = useRoleLog(snapshot.session.id);
  const workspace = snapshot.session.workdir || '';
  const titles = useMemo(() => new Map(snapshot.backlog.map(item => [item.id, item.title || ''])), [snapshot.backlog]);
  const title = useCallback((id: string) => titles.get(id) ?? '', [titles]);
  const working = (connected && snapshot.work_status?.state === 'running' ? snapshot.work_status.roles : [])
    .filter(role => !taskId || snapshot.roles.find(row => row.role === role)?.tasks?.includes(taskId));
  const visible = details || working.length > 0;
  const rows = useMemo(() => visible ? renderFeedRows(taskId ? log.events.filter(event => eventTaskId(event) === taskId) : log.events,
    { locale, showReasoning: true, workspace }).list : [], [visible, log.events, taskId, locale, workspace]);
  if (!visible) return null;
  return <WorkspaceContext.Provider value={workspace}><TaskTitleContext.Provider value={title}>
    <section data-project-work aria-label={locale === 'zh-CN' ? '团队进展' : 'Team progress'}
      className={details ? '' : 'mx-auto w-full max-w-full px-4 py-2 sm:px-6 lg:max-w-[808px]'}>
      <div className="overflow-hidden rounded-lg border border-line/70">
        <RoleLogCollection rows={rows} key={`${snapshot.session.id}:${log.round}:${taskId ?? ''}`}
          active={role => working.includes(role)} />
        {details && !rows.length && !working.length ? <p className="px-4 py-3 text-xs text-ink-faint">{locale === 'zh-CN' ? '本轮暂无工作记录' : 'No work recorded this round'}</p> : null}
      </div>
    </section>
  </TaskTitleContext.Provider></WorkspaceContext.Provider>;
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
  deliveries,
  onOpen,
}: {
  delivery: DeliveryReceipt;
  deliveries: DeliveryReceipt[];
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
  const previewable = files.filter(file => !deliveryFileUpdated(delivery, deliveries, file.path));
  const visual = sid ? previewable.find((file) => modelFormat(file.path)) ?? previewable.find((file) => IMAGE_FILE.test(file.path)) : undefined;
  const others = files.filter(file => file !== visual);
  const listed = others.slice(0, 3);
  const updatedFiles = files.filter(file => deliveryFileUpdated(delivery, deliveries, file.path));
  return (
    <aside
      data-delivery-kind={delivery.kind}
      data-stage={stage || undefined}
      className="result-card mx-auto my-4 w-[calc(100%-2rem)] max-w-full sm:w-[calc(100%-3rem)] lg:max-w-[760px]"
    >
      {visual && sid && modelFormat(visual.path) ? <div className="result-card-preview result-card-model" data-delivery-model={visual.path}>
        <ModelThumbnail delivery={delivery.delivery_id} sid={sid} path={visual.path} label={visual.label}
          onOpen={onOpen ? () => onOpen(delivery, visual.path) : undefined} />
      </div> : visual && sid ? <div className="result-card-preview" data-delivery-thumbnail={visual.path}>
        <WorkspaceImage delivery={delivery.delivery_id} sid={sid} path={visual.path} src={visual.path} alt={visual.label || visual.path}
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
        <p className="text-xs text-ink-faint">{deliveryTimeLabel(delivery, zh)}</p>
        {listed.length ? <ul className="result-card-files">
          {listed.map(file => <li key={file.path} title={file.path}>
            <span aria-hidden="true">{fileGlyph(file.path)}</span>{file.label || file.path.split('/').pop()}
          </li>)}
          {others.length > listed.length ? <li className="text-ink-faint">{zh ? `另有 ${others.length - listed.length} 个文件` : `${others.length - listed.length} more files`}</li> : null}
        </ul> : null}
        {!!updatedFiles.length && <p className="text-xs text-warn" role="status">{zh ? '已被后来的交付更新' : 'Updated by a later delivery'} · {updatedFiles.map(file => file.label || file.path).join('、')}</p>}
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

type TaskCardState = 'running' | 'done' | 'failed' | 'asking' | 'queued' | 'paused' | 'idle';

const TASK_STATE_LABEL: Record<Exclude<TaskCardState, 'running' | 'idle'>, [string, string]> = {
  done: ['已结束', 'Ended'],
  failed: ['没有完成', 'Not finished'],
  asking: ['等你回答', 'Waiting for your answer'],
  queued: ['待开始', 'Up next'],
  paused: ['已停止', 'Stopped'],
};

const TASK_TONE: Partial<Record<TaskCardState, string>> = { running: 'running', done: 'done', failed: 'failed', asking: 'waiting' };
const TASK_RUNNING_LABEL: Record<string, [string, string]> = {
  engineer: ['工程师在做', 'Engineer working'],
  reviewer: ['审阅者在查', 'Reviewer checking'],
  planner: ['规划者在排', 'Planner planning'],
};

/**
 * A task in the conversation: one line on where it stands, and its results.
 */
function TaskWorkCard({ event, onOpenArtifact, onInspect }: { event: EventMsg; onOpenArtifact?: (path: string) => void; onInspect: (id: string) => void }) {
  const { locale } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const sid = useContext(SessionIdContext);
  const state = String(event.state) as TaskCardState;
  const title = String(event.title ?? '');
  const path = (event.result_paths as string[] | undefined)?.[0];
  const progress = String(event.progress ?? '');
  const label = state === 'running' ? (TASK_RUNNING_LABEL[String(event.role)] ?? ['进行中', 'In progress'])[locale === 'zh-CN' ? 0 : 1]
    : state === 'idle' ? '' : TASK_STATE_LABEL[state][locale === 'zh-CN' ? 0 : 1];
  return <section data-task-work={String(event.item_id)} data-state={state}
    className="task-line mx-auto w-full max-w-full px-4 py-1 sm:px-6 lg:max-w-[808px]">
    <div className="task-line-row flex min-w-0 items-center gap-2.5 text-[13px]">
      <span className="status-dot" data-tone={TASK_TONE[state]} aria-hidden="true" />
      <button type="button" aria-expanded={expanded} aria-controls={`task-record-${event.item_id}`}
        title={locale === 'zh-CN' ? '展开或收起往来信件' : 'Expand or collapse letters'}
        className="task-expand min-w-0 flex-1 break-words text-left text-ink-dim hover:text-ink" onClick={() => setExpanded(current => !current)}>
        {label}{locale === 'zh-CN' ? '：' : ': '}{title}
      </button>
    </div>
    {state !== 'done' && progress && <p data-task-progress className="mt-1 break-words pl-4 text-xs leading-relaxed text-ink-dim">{progress}</p>}
    {!expanded && path ? <div className="mt-1 pl-4"><button type="button"
      className="max-w-full break-all text-left text-xs text-blue hover:underline" onClick={() => onOpenArtifact?.(path)}>{path}</button></div> : null}
    {expanded && <div id={`task-record-${event.item_id}`} className="mt-3 min-w-0 border-l border-line pl-4">
      <InlineTaskRecord sid={sid} id={String(event.item_id)} onOpenArtifact={onOpenArtifact} />
      <button type="button" className="my-2 text-xs text-ink-faint hover:text-ink" onClick={() => onInspect(String(event.item_id))}>
        {locale === 'zh-CN' ? '查看任务详情' : 'View task details'}
      </button>
    </div>}
  </section>;
}

/** Mounted only when expanded: the existing detail query owns the record. */
function InlineTaskRecord({ sid, id, onOpenArtifact }: { sid: string | null; id: string; onOpenArtifact?: (path: string) => void }) {
  const { locale } = useI18n();
  const query = useBacklogItem(sid, id);
  return query.isError ? <ErrorMessage error={query.error} /> : query.data
    ? <TaskLetters item={query.data} sid={sid} onOpenArtifact={onOpenArtifact} />
    : <p className="text-xs text-ink-faint">{locale === 'zh-CN' ? '正在读取信件…' : 'Reading letters…'}</p>;
}

/** Render one row in the order supplied by the server. */
function ConversationItem({ row, deliveries, artifacts, onOpenArtifact, onOpenDelivery, doing }: {
  doing: string;
  row: ActivityRow;
  deliveries: DeliveryReceipt[];
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  onOpenDelivery?: (delivery: DeliveryReceipt, path?: string) => void;
}) {
  const delivery = deliveryFromEvent(row.ev);
  const openArtifact = delivery && onOpenDelivery ? (path: string) => onOpenDelivery(delivery, path) : onOpenArtifact;
  return <section className="conversation-thread">
    <ConversationRow doing={row.ev.status === 'running' ? doing : undefined} ev={row.ev} fallbackText={row.r.text} artifacts={artifacts} onOpenArtifact={openArtifact}
      rewritePrompt={row.ev.type === 'ui.operator' && !row.ev.answer_to ? row.r.text : undefined} />
    {delivery && <DeliveryCard delivery={delivery} deliveries={deliveries} onOpen={onOpenDelivery} />}
  </section>;
}

/**
 * The live event feed — a CLEAN, whitelisted stream (matching the terminal
 * cockpit), not a raw event dump. Non-whitelisted events (agent_io.* framing,
 * telemetry, internal bookkeeping) are dropped; provider reasoning is visually quiet.
 * Auto-follows the tail unless the user scrolls up to read history.
 */
export function EventStream({
  events,
  connected,
  showReasoning,
  embedded = false,
  showHeader = true,
  artifacts,
  onOpenArtifact,
  onOpenDelivery,
  snapshot,
  historyStatus = 'ready',
  historyRefreshing = false,
  hasOlder = false,
  loadingOlder = false,
  onLoadOlder,
  refreshingKept = false,
  onRetryHistory,
  onCompose,
  rememberPosition = false,
  onConversationAction,
  requestedMessageTs,
  onMessageLocated,
}: {
  events: EventMsg[];
  connected: boolean;
  showReasoning: boolean;
  embedded?: boolean;
  showHeader?: boolean;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  onOpenDelivery?: (delivery: DeliveryReceipt, path?: string) => void;
  snapshot?: Snapshot;
  historyStatus?: ConversationHistoryStatus;
  historyRefreshing?: boolean;
  /** Showing content kept from an earlier visit while it refreshes. */
  refreshingKept?: boolean;
  /** Offered on a running task's card when it looks stuck. */
  onRetryHistory?: () => void;
  onCompose?: (text: string) => void;
  rememberPosition?: boolean;
  onConversationAction?: (prompt: string) => void;
  requestedMessageTs?: number;
  onMessageLocated?: () => void;
} & Partial<ConversationPagination>) {
  const { locale, t } = useI18n();
  const sessionId = useContext(SessionIdContext);
  const log = useRoleLog(snapshot?.session.id ?? sessionId);
  const doing = useMemo(() => managerDoing(log.events, locale === 'zh-CN'), [log.events, locale]);
  const [following, setFollowing] = useState(true);
  const [inspectingTask, setInspectingTask] = useState<string | null>(null);
  const followingRef = useRef(true);
  const lastScrollTop = useRef(0);
  const [activityTick, setActivityTick] = useState(() => Date.now());
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const restored = useRef(false);
  const searchSeen = useRef<number | undefined>();
  const [readingError, setReadingError] = useState(false);
  // Rendering a long Markdown/event history is interruptible, so incoming
  // provider fragments never take priority over typing or scrolling.
  const deferredEvents = useDeferredValue(events);
  const deliveries = useMemo(() => deferredEvents.flatMap(event => { const receipt = deliveryFromEvent(event); return receipt ? [receipt] : []; }), [deferredEvents]);
  const workspace = snapshot?.session.workdir || '';
  const taskTitle = useMemo(() => {
    const titles = new Map((snapshot?.backlog ?? []).map((item) => [item.id, item.title || '']));
    return (id: string) => titles.get(id) ?? '';
  }, [snapshot?.backlog]);
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

  const rows = useMemo(() => renderFeedRows(deferredEvents.filter(event => event.type === 'ui.operator' || event.type === 'ui.argus'),
    { locale, showReasoning, workspace }), [deferredEvents, locale, showReasoning, workspace]);
  const rowById = new Map(rows.list.map(row => [row.ev, row]));
  const loadEarlier = useRef({ hasOlder, loadingOlder, onLoadOlder });
  loadEarlier.current = { hasOlder, loadingOlder, onLoadOlder };
  const prepend = useRef<{ height: number; top: number } | null>(null);
  useEffect(() => {
    if (!loadingOlder && prepend.current && scroller.current) {
      scroller.current.scrollTop = prepend.current.top + scroller.current.scrollHeight - prepend.current.height;
      lastScrollTop.current = scroller.current.scrollTop;
      prepend.current = null;
    }
  }, [loadingOlder, events]);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const positionKey = rememberPosition && snapshot ? `argus.conversation.position.v1:${snapshot.session.id}` : null;
    // Initial placement and later explicit searches share this one decision.
    // Deferred rows must have caught up before a search can be marked reached.
    const restorePosition = () => {
      if (historyStatus !== 'ready' || deferredEvents !== events || el.clientHeight === 0) return;
      if (restored.current) {
        if (requestedMessageTs === undefined) { searchSeen.current = undefined; return; }
        if (requestedMessageTs === searchSeen.current) return;
      }
      let follow = true;
      if (requestedMessageTs !== undefined) {
        const target = content.current?.querySelector<HTMLElement>(`[data-conversation-ts="${requestedMessageTs}"]`);
        if (!target) {
          const older = loadEarlier.current;
          if (older.hasOlder && !older.loadingOlder) older.onLoadOlder?.();
          return;
        }
        content.current?.querySelectorAll<HTMLElement>('[data-search-hit]').forEach(previous => { delete previous.dataset.searchHit; });
        const brief = target.querySelector<HTMLDetailsElement>('details[data-testid="conversation-brief"]');
        if (brief) brief.open = true;
        target.dataset.searchHit = 'true';
        target.scrollIntoView({ block: 'center' });
        target.focus({ preventScroll: true });
        follow = false;
      } else {
        try {
          const raw = positionKey ? localStorage.getItem(positionKey) : null;
          if (raw) {
            const saved: unknown = JSON.parse(raw);
            if (!saved || typeof saved !== 'object' || !('top' in saved) || typeof saved.top !== 'number'
              || !Number.isFinite(saved.top) || saved.top < 0 || !('following' in saved) || typeof saved.following !== 'boolean') throw new Error('Invalid reading position');
            follow = saved.following;
            if (!follow) el.scrollTop = saved.top;
          }
        } catch { setReadingError(true); }
        if (follow) el.scrollTop = el.scrollHeight;
      }
      restored.current = true;
      searchSeen.current = requestedMessageTs;
      followingRef.current = follow;
      setFollowing(follow);
      lastScrollTop.current = el.scrollTop;
      if (requestedMessageTs !== undefined) onMessageLocated?.();
    };
    restorePosition();
    const onScroll = () => {
      if (!restored.current || el.clientHeight === 0) return;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      // Scroll anchoring may move down during growth. An upward reader
      // movement leaves following; reaching the bottom resumes it.
      followingRef.current = atBottom || (followingRef.current && el.scrollTop >= lastScrollTop.current - 1);
      lastScrollTop.current = el.scrollTop;
      setFollowing(followingRef.current);
      const older = loadEarlier.current;
      if (el.scrollTop < 80 && older.hasOlder && !older.loadingOlder && older.onLoadOlder) {
        followingRef.current = false;
        setFollowing(false);
        prepend.current = { height: el.scrollHeight, top: el.scrollTop };
        older.onLoadOlder();
      }
      if (positionKey) setReadingError(!writeLocalStorage(positionKey, JSON.stringify({ top: el.scrollTop, following: followingRef.current })));
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    let frame: number | null = null;
    const observer = new ResizeObserver(() => {
      restorePosition();
      if (!restored.current || !followingRef.current) return;
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        frame = null;
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
  }, [snapshot?.session.id, historyStatus, rememberPosition, requestedMessageTs, deferredEvents, events, hasOlder, loadingOlder, onMessageLocated]);

  const jump = () => {
    followingRef.current = true;
    setFollowing(true);
    if (scroller.current) {
      scroller.current.scrollTo({ top: scroller.current.scrollHeight, behavior: 'auto' });
      lastScrollTop.current = scroller.current.scrollTop;
    }
  };

  const localMessageId = events.filter(event => event.type === 'ui.operator'
    && event.message_id === `local-${event.request_id}-operator`).at(-1)?.message_id;
  const seenLocalMessage = useRef(localMessageId);
  const pendingLocalJump = useRef(false);
  useEffect(() => {
    // History and server messages have durable identities. Only a new local
    // placeholder from this page resumes following. Remember the intention
    // even if its durable row arrives before the deferred list catches up.
    if (localMessageId && localMessageId !== seenLocalMessage.current) pendingLocalJump.current = true;
    seenLocalMessage.current = localMessageId;
    if (deferredEvents !== events) return;
    if (pendingLocalJump.current) {
      pendingLocalJump.current = false;
      jump();
    }
  }, [localMessageId, deferredEvents, events]);

  return (
    <ConversationActionContext.Provider value={onConversationAction}>
    <WorkspaceContext.Provider value={workspace}>
    <TaskTitleContext.Provider value={taskTitle}>
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
        {hasOlder && <button type="button" disabled={loadingOlder} onClick={onLoadOlder} className="block w-full py-2 text-xs text-ink-faint">{locale === 'zh-CN' ? loadingOlder ? '正在加载…' : '更早的对话' : loadingOlder ? 'Loading…' : 'Earlier conversation'}</button>}
        {deferredEvents.length === 0 ? (
          historyStatus === 'ready' ? onCompose ? <div className="mx-auto max-w-2xl px-5 pb-8 pt-[12vh]">
            <h2 className="font-serif text-[28px] font-medium leading-tight tracking-[-0.01em] text-ink">{locale === 'zh-CN' ? '从你想完成的事开始' : 'Start with what you want to accomplish'}</h2>
            <p className="mt-2 text-sm leading-6 text-ink-faint">{locale === 'zh-CN' ? '直接说出目标，也可以选择下面的场景，再补充你的材料和要求。' : 'Describe your goal, or choose a starting point and add your material and requirements.'}</p>
            <StartPrompts onPick={onCompose} />
            <p className="mt-6 text-xs text-ink-faint">{locale === 'zh-CN' ? '或者直接试一个实际跑过的任务' : 'Or try a task that was run end to end'}</p>
            <ShowcasePrompts onPick={onCompose} />
          </div> : <EmptyHint>{t('stream.ready')}</EmptyHint> : null
        ) : (
          <>
            {deferredEvents.map(event => {
              const key = conversationEventKey(event);
              if (event.type === 'ui.task') return <TaskWorkCard key={key} event={event} onOpenArtifact={onOpenArtifact} onInspect={setInspectingTask} />;
              if (event.type === 'ui.question') return <section key={key} data-conversation-question={String(event.question_id)} className="mx-auto w-full px-6 py-3 lg:max-w-[808px]">
                <MarkdownContent>{questionSentence(roleLabel(String(event.agent_layer), t), taskTitle(eventTaskId(event)) || undefined, String(event.text), locale === 'zh-CN')}</MarkdownContent>
              </section>;
              const row = rowById.get(event);
              return row ? <ConversationItem key={key} doing={doing} row={row} deliveries={deliveries}
                artifacts={artifacts} onOpenArtifact={onOpenArtifact} onOpenDelivery={onOpenDelivery} /> : null;
            })}

          </>
        )}
            {/* Each role's process stays folded until opened. */}
            {snapshot ? <ProjectRoleLogs snapshot={snapshot} connected={connected} /> : null}
        </div>
      </div>
      {!following && (
        <button
          onClick={jump}
          aria-label={t('stream.jumpToLatest')}
          title={t('stream.jumpToLatest')}
          className="my-2 flex h-8 w-8 shrink-0 self-center items-center justify-center rounded-full border border-line/60 bg-panel text-sm text-ink-dim shadow-glow transition-all duration-200 hover:border-ink-faint hover:text-ink"
        >
          ↓
        </button>
      )}
    </section>
    {inspectingTask && <TaskDetailModal sid={snapshot?.session.id ?? sessionId} itemId={inspectingTask} onClose={() => setInspectingTask(null)} onOpenArtifact={onOpenArtifact} />}
    </TaskTitleContext.Provider>
    </WorkspaceContext.Provider>
    </ConversationActionContext.Provider>
  );
}
