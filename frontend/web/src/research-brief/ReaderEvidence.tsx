import type { ArtifactInfo } from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { RawDisclosure } from '../components/primitives';
import { useI18n } from '../i18n';
import { plainDetail, plainEventName } from '../lib/plainStatus';
import { readableRecord } from '../map/submap';
import { evidenceDates, hasTruncatedFields, type EvidenceState, type EvidenceTask, type ReaderEvidenceSelection, type UsedEvidence } from './evidence';

type ReadingArtifacts = { artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void };
const prose = (value: unknown) => typeof value === 'string' ? value : '';

function SourceJson({ record, kind = 'event' }: { record: Record<string, unknown>; kind?: 'task' | 'excerpt' | 'event' | 'full-record' }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const label = kind === 'full-record' ? zh ? '查看同版本完整记录' : 'View the complete record from the same version'
    : kind === 'task' ? zh ? '任务材料（JSON）' : 'Task material (JSON)'
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
  const ts = typeof record?.ts === 'number' && Number.isFinite(record.ts) && record.ts > 0 ? record.ts : undefined;
  const text = plainDetail(readableRecord(prose(record?.text) || prose(record?.reason)), locale).text;
  return <section className="mt-3 border-t border-line/50 pt-3" data-event-id={row.id} data-event-revision={row.revision || ''}
    data-evidence-state={currentReason ? 'current' : row.state} data-evidence-reason={currentReason}>
    <h3 className="text-xs font-medium text-ink">{plainEventName(kind, locale) || kind || (zh ? '来源记录' : 'Source record')}</h3>
    {record ? <>
      <div className="mb-2 flex flex-wrap gap-x-2 text-[11px] text-ink-faint">
        {ts ? <time dateTime={new Date(ts * 1000).toISOString()}>{new Date(ts * 1000).toLocaleString(locale)}</time> : null}
        {kind ? <code>{kind}</code> : null}
      </div>
      {currentReason === 'changed' ? <p className="text-xs text-ink-faint">{zh ? '这条记录的内容已有更新，下方另列供参考。' : 'This record has been updated and is listed separately for reference.'}</p> : null}
      {currentReason === 'unverified' ? <p className="text-xs text-ink-faint">{zh ? '无法确认下方内容是否与当时材料一致。' : 'The text below cannot be confirmed as matching the material used at the time.'}</p> : null}
      {text ? <MarkdownContent {...artifacts}>{text}</MarkdownContent> : null}
      {prose(record.next_action) ? <><p className="mt-2 text-xs font-medium text-ink">{zh ? '记录中的下一步' : 'Recorded next action'}</p><MarkdownContent {...artifacts}>{prose(record.next_action)}</MarkdownContent></> : null}
      {hasTruncatedFields(record) ? <p className="text-xs text-ink-faint">{zh ? '保留的是材料节选，部分内容已截短。' : 'This retained excerpt includes shortened content.'}</p> : null}
      <SourceJson record={record} kind={row.state === 'snapshot' ? 'excerpt' : 'event'} />
      {row.state === 'snapshot' ? row.fullRecord ? <SourceJson record={{ ...row.fullRecord }} kind="full-record" />
        : <p className="text-xs text-ink-faint">{zh ? '当前无法核对同版本的完整来源记录。' : 'A complete source record from the same version cannot currently be verified.'}</p> : null}
    </> : <p className="text-xs text-ink-faint">{unavailable(row.state, zh)}</p>}
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
