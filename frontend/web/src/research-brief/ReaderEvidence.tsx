import type { ArtifactInfo } from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { RawDisclosure } from '../components/primitives';
import { useI18n } from '../i18n';
import { plainEventName, plainStatus } from '../lib/plainStatus';
import type { CardSourceSnapshot } from '../map/presentation';
import { completionScope } from '../map/status';
import { evidenceDates, hasTruncatedFields, type EvidenceState, type EvidenceTask, type ReaderEvidenceSelection, type UsedEvidence } from './evidence';

type ReadingArtifacts = { artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void };
const prose = (value: unknown) => typeof value === 'string' ? value : '';

function RecordHeading({ record }: { record: Record<string, unknown> }) {
  const { locale, t } = useI18n();
  const zh = locale === 'zh-CN', kind = prose(record.type), role = prose(record.role);
  const name = plainEventName(kind, locale);
  const ts = typeof record.ts === 'number' && Number.isFinite(record.ts) && record.ts > 0 ? record.ts : undefined;
  return <>
    <h4 className="text-xs font-medium text-ink">{name && name !== kind ? name : zh ? '来源记录' : 'Source record'}</h4>
    <div className="mb-2 flex flex-wrap gap-x-2 text-[11px] text-ink-faint">
      {role ? <span>{['manager', 'planner', 'engineer', 'reviewer'].includes(role) ? t(`role.${role}`) : role}</span> : null}
      {ts ? <time dateTime={new Date(ts * 1000).toISOString()}>{new Date(ts * 1000).toLocaleString(locale)}</time> : null}
      {typeof record.attempt === 'number' ? <span>{zh ? `第 ${record.attempt} 次尝试` : `Attempt ${record.attempt}`}</span> : null}
      {typeof record.round_index === 'number' ? <span>{zh ? `第 ${record.round_index} 轮` : `Round ${record.round_index}`}</span> : null}
    </div>
  </>;
}

/** Source prose stays intact, including recorded handoffs and actions in the body. */
function RecordText({ record, ...artifacts }: ReadingArtifacts & { record: Record<string, unknown> }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', text = prose(record.text) || prose(record.reason);
  const scope = completionScope({ id: prose(record.id), item_id: prose(record.item_id), type: prose(record.type),
    ts: typeof record.ts === 'number' ? record.ts : 0, text,
    ...(typeof record.overall_complete === 'boolean' ? { overall_complete: record.overall_complete } : {}),
    ...(typeof record.campaign_continues === 'boolean' ? { campaign_continues: record.campaign_continues } : {}),
  }, zh);
  return <>
    {scope ? <p className="mb-1 text-xs text-ink-faint">{scope}</p> : null}
    {record.review_skipped === true ? <p className="mb-1 text-xs text-ink-faint">{zh ? '这条记录标记为本轮未审阅。' : 'This record marks the round as not reviewed.'}</p>
      : record.review_source === 'engineer_self_review' ? <p className="mb-1 text-xs text-ink-faint">{zh ? '这条记录来自执行者自检。' : 'This record is the executor’s self-check.'}</p> : null}
    {text ? <MarkdownContent {...artifacts}>{text}</MarkdownContent> : null}
    {prose(record.next_action) ? <><p className="mt-2 text-xs font-medium text-ink">{zh ? '记录中的下一步' : 'Recorded next action'}</p><MarkdownContent {...artifacts}>{prose(record.next_action)}</MarkdownContent></> : null}
    {hasTruncatedFields(record) ? <p className="text-xs text-ink-faint">{zh ? '保留的是材料节选，部分内容已截短。' : 'This retained excerpt includes shortened content.'}</p> : null}
  </>;
}

function TaskState({ record, current, capturedAt, ...artifacts }: ReadingArtifacts & {
  record: Record<string, unknown>; current: boolean; capturedAt?: number;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', status = prose(record.status), question = prose(record.pending_question);
  return <div data-reader-fact-state={current ? 'current' : 'retained'} className="mt-2">
    <p className="text-xs text-ink-dim">
      {current ? zh ? '当前已加载的任务状态：' : 'Currently loaded task status: ' : zh ? '说明生成时的任务状态：' : 'Task status retained at generation: '}
      {status ? plainStatus(status, locale) : zh ? '未记录' : 'Not recorded'}
      {typeof record.attempt === 'number' ? <span className="ml-2 text-ink-faint">{zh ? `第 ${record.attempt} 次尝试` : `Attempt ${record.attempt}`}</span> : null}
      {typeof capturedAt === 'number' && Number.isFinite(capturedAt) && capturedAt > 0 ? <time className="ml-2 text-ink-faint" dateTime={new Date(capturedAt * 1000).toISOString()}>{new Date(capturedAt * 1000).toLocaleString(locale)}</time> : null}
    </p>
    {question ? <div className="mt-1"><p className="text-xs font-medium text-ink">{current ? zh ? '任务记录中待你补充的问题' : 'Question awaiting your input in the task record' : zh ? '当时记录的待补充问题' : 'Question retained from that time'}</p><MarkdownContent {...artifacts}>{question}</MarkdownContent>
      {record.pending_question_truncated === true ? <p className="text-xs text-ink-faint">{zh ? '这里保留的是问题节选。' : 'Only an excerpt of the question is retained here.'}</p> : null}</div> : null}
  </div>;
}

function FactRecord({ row, current = false, ...artifacts }: ReadingArtifacts & { row: UsedEvidence; current?: boolean }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  // The evidence selector supplies a full record only after matching task, id and revision.
  const record: Record<string, unknown> | undefined = row.fullRecord ? { ...row.fullRecord } : row.record;
  const expandedSource = row.fullRecord && row.record && (hasTruncatedFields(row.record)
    || prose(row.record.text) !== row.fullRecord.text || prose(row.record.next_action) !== prose(row.fullRecord.next_action));
  return <div className="mt-3 min-w-0 border-l-2 border-line pl-3" data-reader-fact-event={row.id}
    data-reader-fact-source={current ? 'current' : row.state} data-reader-fact-revision={row.revision || ''}>
    {record ? <>
      <RecordHeading record={record} />
      {expandedSource ? <p className="mb-1 text-xs text-ink-faint">{zh ? '这里展示已核对的同版本完整记录；说明生成时只使用了节选。' : 'This is the verified complete record from the same version; generation used only an excerpt.'}</p> : null}
      <RecordText record={record} {...artifacts} />
    </> : <p className="text-xs text-ink-faint">{unavailable(row.state, zh)}</p>}
  </div>;
}

/** Recorded state and reports do not depend on generated explanation prose. */
export function ReaderTaskFacts({ selection, ...artifacts }: ReadingArtifacts & { selection: ReaderEvidenceSelection }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const retained = selection.mode === 'snapshot' ? selection.usedTask?.record : undefined;
  const current = selection.currentTask ?? selection.usedTask?.fullRecord;
  return <section className="mt-4 min-w-0 border-t border-line/60 pt-3 text-[13px] leading-6 text-ink-dim"
    data-reader-task-facts={selection.taskId} data-reader-fact-card={selection.cardKey}>
    <h3 className="text-xs font-medium text-ink">{zh ? '任务状态' : 'Recorded task status'}</h3>
    {retained ? <TaskState record={retained} current={false} capturedAt={selection.snapshot?.captured_at} {...artifacts} /> : null}
    {current ? <TaskState record={{ ...current }} current {...artifacts} /> : null}
    {!retained && !current ? <p className="mt-1 text-xs text-ink-faint">{zh ? '可核对的任务状态尚未加载。' : 'A verifiable task status has not been loaded.'}</p> : null}
    <h3 className="mt-3 text-xs font-medium text-ink">{zh ? '记录中的结果与后续' : 'Recorded results and follow-up'}</h3>
    {selection.used.length ? <div data-reader-fact-group="used">
      <p className="text-xs text-ink-faint">{zh ? '以下是这份说明对应的来源原文，按记录保留各自的时间与归属。' : 'These are the source reports for this explanation, with their recorded dates and attribution.'}</p>
      {selection.snapshot?.events_truncated ? <p className="text-xs text-ink-faint">{zh ? '生成材料只包含部分选中记录。' : 'The generation material contains only part of the selected records.'}</p> : null}
      {selection.used.map((row, index) => <FactRecord key={`${row.id}:${index}`} row={row} {...artifacts} />)}
    </div> : <p className="text-xs text-ink-faint">{zh ? '这份说明没有可核对的来源事件。' : 'No verifiable source events are retained for this explanation.'}</p>}
    {selection.current.length ? <div className="mt-3" data-reader-fact-group="current">
      <p className="text-xs font-medium text-ink">{zh ? '另外加载的记录 · 与上方说明原文分开' : 'Additional loaded records · separate from the explanation sources'}</p>
      {selection.current.map(({ record }, index) => <FactRecord key={`${record.id}:${index}`} current
        row={{ id: record.id, revision: record.revision, state: 'unverified', record: { ...record } }} {...artifacts} />)}
    </div> : null}
  </section>;
}

function SourceJson({ record, kind = 'event' }: { record: Record<string, unknown>; kind?: 'task' | 'related-task' | 'excerpt' | 'event' | 'full-record' }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const label = kind === 'full-record' ? zh ? '查看同版本完整记录' : 'View the complete record from the same version'
    : kind === 'task' ? zh ? '任务材料（JSON）' : 'Task material (JSON)'
    : kind === 'related-task' ? zh ? '相邻任务材料（JSON）' : 'Related task material (JSON)'
    : kind === 'excerpt' ? zh ? '生成时材料节选（JSON）' : 'Retained source excerpt (JSON)'
      : zh ? '原始记录（JSON）' : 'Original record (JSON)';
  const disclosure = <RawDisclosure label={label}>
    {kind === 'full-record' ? <p className="text-xs text-ink-faint">{zh ? '生成时只使用了上方保存的材料节选；此处保留同版本完整记录供查阅。' : 'Generation used only the retained excerpt above; this complete record from the same version is available for reference.'}</p> : null}
    <pre data-evidence-json={kind} data-evidence-full-json={kind === 'full-record' || undefined} className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(record, null, 2)}</pre>
  </RawDisclosure>;
  return kind === 'full-record' ? <div data-evidence-full-record>{disclosure}</div> : disclosure;
}

function unavailable(state: EvidenceState, zh: boolean) {
  if (state === 'changed') return zh ? '内容已更新，无法确认当时原文。' : 'The content has changed, so the original text cannot be confirmed.';
  if (state === 'owner-mismatch') return zh ? '记录的任务归属不匹配，未将其列为来源原文。' : 'The record belongs to a different task and is not shown as source text.';
  if (state === 'missing') return zh ? '这条来源原文尚未加载；不能用其他记录替代。' : 'This original source is not loaded; another record cannot replace it.';
  return zh ? '保留的信息不足，无法确认当时原文。' : 'The retained information is insufficient to confirm the original text.';
}

export function ReaderEvidenceSummary({ selection }: { selection: ReaderEvidenceSelection }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', dates = evidenceDates(selection);
  const known = selection.used.filter(row => row.record).length;
  return <div className="text-xs text-ink-faint" data-evidence-summary>
    <p>{selection.mode === 'snapshot'
      ? zh ? `这份说明保留了 ${known} 条来源材料节选。` : `This explanation retains ${known} source excerpts.`
      : selection.mode === 'legacy' ? zh ? `旧说明来源：已核对 ${known}/${selection.used.length} 条记录。` : `Earlier sources: ${known}/${selection.used.length} records verified.`
        : zh ? '尚无已生成说明；当前已加载记录另列。' : 'No explanation has been generated; current records are listed separately.'}</p>
    {dates.length ? <p data-evidence-range>{zh ? '来源记录时间：' : 'Source record dates: '}{dates.map((ts, index) => <span key={index}>{index ? ' – ' : ''}<time dateTime={new Date(ts * 1000).toISOString()}>{new Date(ts * 1000).toLocaleString(locale)}</time></span>)}</p> : null}
  </div>;
}

function TaskMaterial({ group, taskId, record, fullRecord, state, ...artifacts }: ReadingArtifacts & {
  group: 'used' | 'current'; taskId: string; record?: Record<string, unknown>; fullRecord?: EvidenceTask['fullRecord']; state?: EvidenceState;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  return <section className="mt-3" data-evidence-task={group} data-task-id={taskId} data-evidence-state={state || 'current'}>
    <h3 className="mb-1 text-xs font-medium text-ink">{group === 'used' ? zh ? '说明使用的任务目标' : 'Task goal used for this explanation' : zh ? '当前已加载的任务记录' : 'Currently loaded task record'}</h3>
    {record ? <>
      {prose(record.title) ? <p className="text-xs text-ink-faint">{prose(record.title)}</p> : null}
      {prose(record.objective) ? <MarkdownContent {...artifacts}>{prose(record.objective)}</MarkdownContent>
        : <p className="text-xs text-ink-faint">{zh ? '这份材料没有提供任务目标。' : 'This material does not include a task goal.'}</p>}
      {hasTruncatedFields(record) ? <p className="text-xs text-ink-faint">{zh ? '保留的是材料节选，部分内容已截短。' : 'This retained excerpt includes shortened content.'}</p> : null}
      <SourceJson record={record} kind="task" />
      {state === 'snapshot' ? fullRecord ? <SourceJson record={{ ...fullRecord }} kind="full-record" />
        : <p className="text-xs text-ink-faint">{zh ? '当前无法核对同版本的完整任务记录。' : 'A complete task record from the same version cannot currently be verified.'}</p> : null}
    </> : <p className="text-xs text-ink-faint">{zh ? '旧说明未保留当时目标，现有记录无法核实。' : 'The earlier goal was not retained and cannot be confirmed from the current record.'}</p>}
  </section>;
}

function EventMaterial({ row, currentReason, ...artifacts }: ReadingArtifacts & { row: UsedEvidence; currentReason?: string }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', record = row.record;
  const kind = prose(record?.type);
  return <section className="mt-3 border-t border-line/50 pt-3" data-event-id={row.id} data-event-revision={row.revision || ''}
    data-evidence-state={currentReason ? 'current' : row.state} data-evidence-reason={currentReason}>
    {record ? <>
      <RecordHeading record={record} />
      {kind ? <code className="text-[11px] text-ink-faint">{kind}</code> : null}
      {currentReason === 'changed' ? <p className="text-xs text-ink-faint">{zh ? '这条记录的内容已有更新，下方另列供参考。' : 'This record has been updated and is listed separately for reference.'}</p> : null}
      {currentReason === 'unverified' ? <p className="text-xs text-ink-faint">{zh ? '无法确认下方内容是否与当时材料一致。' : 'The text below cannot be confirmed as matching the material used at the time.'}</p> : null}
      <RecordText record={record} {...artifacts} />
      <SourceJson record={record} kind={row.state === 'snapshot' ? 'excerpt' : 'event'} />
      {row.state === 'snapshot' ? row.fullRecord ? <SourceJson record={{ ...row.fullRecord }} kind="full-record" />
        : <p className="text-xs text-ink-faint">{zh ? '当前无法核对同版本的完整来源记录。' : 'A complete source record from the same version cannot currently be verified.'}</p> : null}
    </> : <p className="text-xs text-ink-faint">{unavailable(row.state, zh)}</p>}
  </section>;
}

function RelatedTaskMaterial({ snapshot, ...artifacts }: ReadingArtifacts & { snapshot: CardSourceSnapshot }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', tasks = snapshot.related_tasks ?? [];
  if (!tasks.length && !snapshot.related_tasks_truncated) return null;
  return <section className="mt-3 border-t border-line/50 pt-3" data-evidence-related-tasks>
    <p className="text-xs text-ink-faint">{zh ? '相邻任务不代表本项交接。以下仅展示生成时保存的相邻任务材料。' : 'Related tasks do not establish a handoff from this task. Only related task material retained at generation time is shown below.'}</p>
    {snapshot.related_tasks_truncated ? <p className="text-xs text-ink-faint">{zh ? '生成材料只包含部分相邻任务。' : 'The generation material contains only some related tasks.'}</p> : null}
    <RawDisclosure label={zh ? `生成时保存的相邻任务（${tasks.length}）` : `Related tasks retained at generation time (${tasks.length})`}>
      {tasks.map((record, index) => <section key={`${record.id}:${index}`} className="mt-3 border-t border-line/50 pt-3" data-evidence-related-task={record.id}>
        {prose(record.title) ? <h3 className="text-xs font-medium text-ink">{prose(record.title)}</h3> : null}
        <p className="text-xs text-ink-faint">{zh ? '任务 ID：' : 'Task ID: '}<code className="break-all">{record.id}</code></p>
        {prose(record.objective) ? <MarkdownContent {...artifacts}>{prose(record.objective)}</MarkdownContent> : null}
        {prose(record.status) ? <p className="text-xs text-ink-faint">{zh ? '保存时状态：' : 'Retained status: '}{plainStatus(prose(record.status), locale)}</p> : null}
        {Array.isArray(record.deps) ? <p className="text-xs text-ink-faint">{zh ? '依赖任务：' : 'Dependencies: '}{record.deps.length
          ? record.deps.filter((id): id is string => typeof id === 'string').map((id, depIndex) => <span key={`${id}:${depIndex}`}>{depIndex ? ', ' : ''}<code className="break-all">{id}</code></span>)
          : zh ? '无' : 'None'}</p> : null}
        {hasTruncatedFields(record) ? <p className="text-xs text-ink-faint">{zh ? '保留的是材料节选，部分内容已截短。' : 'This retained excerpt includes shortened content.'}</p> : null}
        <SourceJson record={record} kind="related-task" />
      </section>)}
    </RawDisclosure>
  </section>;
}

/** The same source grouping is used by current research and historical task/step readers. */
export function ReaderEvidence({ selection, showSummary = true, ...artifacts }: ReadingArtifacts & {
  selection: ReaderEvidenceSelection; showSummary?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  return <div className="text-[13px] leading-6 text-ink-dim" data-testid="reader-evidence" data-evidence-mode={selection.mode}
    data-evidence-card-key={selection.cardKey} data-evidence-task-id={selection.taskId}>
    {showSummary ? <ReaderEvidenceSummary selection={selection} /> : null}
    <div data-evidence-group="used">
      <h2 className="mt-3 text-sm font-medium text-ink">{zh ? '这份说明使用的材料' : 'Material used for this explanation'}</h2>
      {selection.invalidSnapshot ? <p className="text-xs text-ink-faint">{zh ? '保存的材料与此任务或环节不匹配，以下仅显示能够核对的记录。' : 'The saved material does not match this task or step; only verifiable records are shown below.'}</p> : null}
      {selection.mode === 'snapshot' ? <p className="text-xs text-ink-faint">{zh ? '这是生成时保存的材料节选，不会被后来的记录替换。' : 'These are retained excerpts from generation time; later records do not replace them.'}</p> : null}
      {selection.snapshot && Number.isFinite(selection.snapshot.captured_at) && selection.snapshot.captured_at > 0 ? <p className="text-xs text-ink-faint">{zh ? '材料保留时间：' : 'Material retained: '}<time data-evidence-captured-at dateTime={new Date(selection.snapshot.captured_at * 1000).toISOString()}>{new Date(selection.snapshot.captured_at * 1000).toLocaleString(locale)}</time></p> : null}
      {selection.mode === 'legacy' ? <p className="text-xs text-ink-faint">{zh ? '以下只显示能够核对的来源记录与任务目标；当时选用的具体文字没有完整保留。' : 'Only verifiable source records and the task goal are shown; the exact excerpts supplied at the time were not retained.'}</p> : null}
      {selection.snapshot?.events_truncated ? <p className="text-xs text-ink-faint">{zh ? '生成材料只包含部分选中记录。' : 'The generation material contains only part of the selected records.'}</p> : null}
      {selection.usedTask ? <TaskMaterial group="used" taskId={selection.taskId} {...selection.usedTask} {...artifacts} /> : null}
      {selection.used.map((row, index) => <EventMaterial key={`${row.id}:${index}`} row={row} {...artifacts} />)}
      {!selection.used.length ? <p className="text-xs text-ink-faint">{zh ? '没有已记录的说明来源事件。' : 'No source events are recorded for this explanation.'}</p> : null}
      {selection.snapshot ? <RelatedTaskMaterial snapshot={selection.snapshot} {...artifacts} /> : null}
    </div>
    <div className="mt-4 border-t border-line pt-3" data-evidence-group="current">
      <h2 className="text-sm font-medium text-ink">{zh ? '当前记录 · 不作为上方说明的来源' : 'Current records · not attributed to the explanation above'}</h2>
      {selection.currentTask ? <TaskMaterial group="current" taskId={selection.taskId} record={{ ...selection.currentTask }} {...artifacts} /> : null}
      {selection.current.map(({ record, reason }, index) => <EventMaterial key={`${record.id}:${index}`} currentReason={reason}
        row={{ id: record.id, revision: record.revision, state: 'unverified', record: { ...record } }} {...artifacts} />)}
      {!selection.currentTask && !selection.current.length ? <p className="text-xs text-ink-faint">{zh ? '当前没有额外已加载的任务记录。' : 'No additional task records are loaded.'}</p> : null}
    </div>
  </div>;
}
