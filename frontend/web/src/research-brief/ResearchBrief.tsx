import { useEffect, useRef, useState } from 'react';
import { BookOpen, ChevronDown, MessageCircle, RefreshCw } from 'lucide-react';
import type { MissionView, Snapshot } from '../../../core/src/types';
import { Button, RawDisclosure, Spinner } from '../components/primitives';
import { MarkdownContent } from '../components/MarkdownContent';
import { Modal, ModalHeader } from '../components/Modal';
import { useI18n } from '../i18n';
import { plainDetail, plainEventName, plainStatus } from '../lib/plainStatus';
import { readableRecord } from '../map/submap';
import { questionAboutStep } from './model';
import { useResearchBrief } from './useResearchBrief';
import { DirectionExample, offersDirectionExample } from './DirectionExample';

export interface ResearchBriefProps {
  sid: string;
  snapshot: Snapshot;
  view: MissionView;
  active: boolean;
  readOnly?: boolean;
  compact?: boolean;
  onAsk?: (draft: string) => void;
}

function ShortText({ value, expandLabel, clamp = true }: { value: string; expandLabel: string; clamp?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [overflow, setOverflow] = useState(false);
  const content = useRef<HTMLDivElement>(null);
  const { locale } = useI18n();
  useEffect(() => {
    const element = content.current;
    if (!element || !clamp || expanded) return;
    const measure = () => setOverflow(element.scrollHeight > element.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [value, clamp, expanded]);
  return <div>
    <div ref={content} className={`text-[13px] leading-6 text-ink-dim ${!clamp || expanded ? '' : 'line-clamp-3'}`}><MarkdownContent>{value}</MarkdownContent></div>
    {clamp && (overflow || expanded) ? <button type="button" className="mt-0.5 text-xs text-blue-sky hover:underline" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
      {expanded ? locale === 'zh-CN' ? '收起' : 'Show less' : expandLabel}<ChevronDown size={11} className={`ml-1 inline ${expanded ? 'rotate-180' : ''}`} />
    </button> : null}
  </div>;
}

export default function ResearchBrief(props: ResearchBriefProps) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const text = (chinese: string, english: string) => zh ? chinese : english;
  const compact = props.compact === true;
  const [readingOpen, setReadingOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setReadingOpen(false);
    setEvidenceOpen(false);
    if (body.current) body.current.scrollTop = 0;
  }, [props.view.mission.id]);
  const result = useResearchBrief({ ...props, locale: zh ? 'zh-CN' : 'en-US' });
  const { task, evidence, brief, card } = result;
  const title = (brief && !result.needsUpdate ? card?.title : undefined) || task?.title || props.view.mission.title || text('当前任务', 'Current task');
  const objective = task?.objective || props.view.mission.objective || props.snapshot.session.objective;
  const generatedAt = typeof card?.generated_at === 'number' && Number.isFinite(card.generated_at)
    ? new Date(card.generated_at * 1000).toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
  const unavailable = result.legacy || result.generationUnavailable || (!result.loading && !result.generationAvailable);
  const hasProblem = !!result.readError || !!result.generationError || unavailable;
  const explanationStatus = result.generating ? <span role="status" className="inline-flex items-center gap-1.5 text-xs text-ink-faint"><Spinner />{text('正在整理说明', 'Preparing an explanation')}</span>
    : generatedAt ? <span className="text-[11px] text-ink-faint">{generatedAt}{result.needsUpdate ? text(' · 待更新', ' · update pending') : ''}</span> : null;

  const boundary = text('背景教学不计作研究进展；子任务完成不表示整个目标已经解决。', 'Background explanations are not research progress; finishing one task does not establish the overall goal.');
  const explanation = <>
    {brief ? <div className="mt-3 grid gap-3 sm:grid-cols-2">
      <div><h3 className="mb-0.5 text-xs font-medium text-ink">{text('这一步为什么有用', 'Why this step helps')}</h3><ShortText key={`why:${task?.id}`} value={brief.why} expandLabel={text('完整说明', 'Full explanation')} /></div>
      <div><h3 className="mb-0.5 text-xs font-medium text-ink">{text('结论到哪里为止', 'What this does and does not establish')}</h3><ShortText key={`scope:${task?.id}`} value={brief.scope} expandLabel={text('完整适用范围', 'Full scope')} clamp={false} /></div>
      <div><h3 className="mb-0.5 text-xs font-medium text-ink">{text('记录中的下一步', 'The recorded next step')}</h3><ShortText key={`next:${task?.id}`} value={brief.next} expandLabel={text('完整下一步', 'Full next step')} /></div>
      {result.teachingUnavailable ? <p className="text-xs text-ink-faint sm:col-span-2">{text('这个概念的说明还没核对清楚，可以继续问这一步。', 'The explanation of this concept has not been checked clearly yet. You can keep asking about this step.')}</p> : null}
      {brief.concept ? <div><h3 className="mb-0.5 text-xs font-medium text-ink">{text('认识一个概念', 'One useful concept')} · {brief.concept.name}</h3>
        <ShortText key={`concept:${task?.id}:${brief.concept.name}`} value={brief.concept.explanation} expandLabel={text('完整定义', 'Full definition')} />
        <details className="mt-1 text-xs text-ink-faint"><summary className="cursor-pointer hover:text-ink">{text('看一个示意例子', 'See an illustrative example')}</summary><div className="mt-2 space-y-2 text-[13px] leading-6 text-ink-dim"><MarkdownContent>{brief.concept.example}</MarkdownContent><MarkdownContent>{brief.concept.connection}</MarkdownContent><p className="text-xs text-ink-faint">{text('这是帮助理解的背景说明，不是本次证明或实验结果。', 'This is background for understanding, not a proof or experiment produced by this task.')}</p></div></details>
        {offersDirectionExample(brief.concept) ? <RawDisclosure label={text('动手看两个方向', 'Try two directions')}><DirectionExample /></RawDisclosure> : null}
      </div> : null}
    </div> : <div className="mt-3 text-[13px] leading-6 text-ink-dim">
      <ShortText value={objective || text('任务目标尚未记录。', 'The task objective has not been recorded yet.')} expandLabel={text('完整任务目标', 'Full task objective')} />
      <p className="mt-1 text-xs text-ink-faint">{result.loading ? text('正在读取任务记录，原始目标会一直保留。', 'Loading the task records; the original objective remains visible.')
        : result.generating ? text('正在根据任务记录整理说明，可以先阅读原始目标。', 'Preparing an explanation from the task records; you can read the original objective meanwhile.')
        : props.readOnly ? text('此处还没有阅读说明。只读模式可以查看已有记录，不会发起解释生成。', 'No explanation is available yet. Read-only mode shows existing records without generating new text.')
        : text('解说暂不可用，可以先查看任务目标与依据。', 'An explanation is not available yet. You can still view the task objective and evidence.')}</p>
    </div>}
    {result.generating && brief ? <p className="mt-2 text-[11px] text-ink-faint">{text('正在依据新记录更新；上方暂时保留之前的说明。', 'Updating from new records; the previous explanation remains visible above.')}{generatedAt ? ` ${generatedAt}` : ''}</p> : null}
    {hasProblem ? <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-faint" role="status"><span>{result.readError ? text('记录暂时读取失败；已显示内容仍保留。', 'Records could not be refreshed; previously loaded content is retained.')
      : result.generationError ? text('说明生成未完成；不会自动重复请求。', 'The explanation could not be prepared. This request will not be repeated automatically.')
        : text('解说服务暂不可用；任务记录不受影响。', 'Explanations are temporarily unavailable; the task records remain available.')}</span>
      {props.active && (!props.readOnly || result.readError) ? <Button className="inline-flex items-center gap-1 text-xs" disabled={result.generating || result.loading} onClick={() => void result.retry()}><RefreshCw size={12} />{text('重试', 'Retry')}</Button> : null}</div> : null}
  </>;

  return <><section className={`mx-4 flex min-h-0 flex-col overflow-hidden rounded-lg border border-line/70 bg-panel ${compact ? 'my-2 px-3 py-2' : 'my-3 max-h-[38vh] px-4 py-3'}`} aria-label={text('读懂这一步', 'Understand this step')} data-testid="research-brief" data-project-id={props.sid} data-task-id={props.view.mission.id} data-compact={compact}>
    {!compact ? <>
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2"><BookOpen size={16} className="shrink-0 text-blue-sky" /><h2 className="text-sm font-semibold text-ink">{text('读懂这一步', 'Understand this step')}</h2></div>
        {explanationStatus}
      </header>
      <div ref={body} className="min-h-0 overflow-y-auto overscroll-contain scroll-thin" data-testid="research-brief-body" tabIndex={0} aria-label={text('任务说明', 'Task explanation')}>
        <div className="mt-1 text-xs text-ink-faint"><MarkdownContent>{title || props.view.mission.title}</MarkdownContent></div>
        {explanation}
      </div>
    </> : null}
    <footer className={compact ? 'shrink-0' : 'mt-2 shrink-0 border-t border-line/50 pt-2'} data-testid="research-brief-footer">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {compact ? <Button className="inline-flex items-center gap-1 text-xs" onClick={() => setReadingOpen(true)}><BookOpen size={12} />{text('阅读说明', 'Read explanation')}</Button> : null}
        <Button className="inline-flex items-center gap-1 text-xs" onClick={() => setEvidenceOpen(true)}><BookOpen size={12} />{text('查看依据', 'View evidence')}</Button>
        {props.onAsk && task && !props.readOnly ? <Button className="inline-flex items-center gap-1 text-xs" onClick={() => props.onAsk?.(questionAboutStep(props.sid, task, evidence, zh))}><MessageCircle size={12} />{compact && !zh ? <>Ask<span className="sr-only"> about this step</span></> : text('继续问这一步', 'Ask about this step')}</Button> : null}
      </div>
      {!compact ? <p className="mt-1 text-[11px] text-ink-faint">{boundary}</p> : null}
    </footer>
  </section>
    <Modal open={readingOpen} onClose={() => setReadingOpen(false)} label={text('任务说明', 'Task explanation')}>
      <ModalHeader title={text('读懂这一步', 'Understand this step')} sub={title || props.view.mission.title} />
      <div className="px-6 pb-6" data-testid="research-brief-reading">{explanationStatus}{explanation}<p className="mt-3 border-t border-line/50 pt-2 text-[11px] text-ink-faint">{boundary}</p></div>
    </Modal>
    <Modal open={evidenceOpen} onClose={() => setEvidenceOpen(false)} label={text('这一步的依据', 'Evidence for this step')}>
      <ModalHeader title={text('这一步的依据', 'Evidence for this step')} sub={task?.title || props.view.mission.title} />
      <div className="space-y-4 px-6 pb-6 text-[13px] leading-6 text-ink-dim">
        <section><h3 className="mb-1 text-sm font-medium text-ink">{text('任务目标', 'Task objective')}</h3><MarkdownContent>{objective || title}</MarkdownContent></section>
        {evidence.map(event => {
          const kind = event.type === 'life.mission.started' ? 'mission_started' : event.type === 'life.planner.task_added' ? 'task_added' : '';
          const label = plainEventName(event.type, locale) || plainStatus('', locale, kind) || event.type;
          const prose = plainDetail(readableRecord(event.text || event.reason), locale).text;
          const timestamp = Number.isFinite(event.ts) && event.ts > 0 ? new Date(event.ts * 1000) : null;
          return <section key={event.id} data-event-id={event.id} className="border-t border-line/50 pt-3">
            <h3 className="text-sm font-medium text-ink">{label}</h3>
            <div className="mb-2 flex flex-wrap gap-x-2 text-[11px] text-ink-faint"><time dateTime={timestamp?.toISOString()}>{timestamp ? timestamp.toLocaleString(locale) : text('时间未记录', 'Time not recorded')}</time><code>{event.type}</code></div>
            <MarkdownContent>{prose || text('这条记录没有附加说明。', 'No additional explanation was recorded.')}</MarkdownContent>
            <RawDisclosure label={text('原始记录（JSON）', 'Original record (JSON)')}><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(event, null, 2)}</pre></RawDisclosure>
          </section>;
        })}
        {!evidence.length ? <p className="text-xs text-ink-faint">{text('当前尚无已加载的任务开始或审阅回执；不据此推断任务结果。', 'No task-start or review records are loaded yet; this does not establish a task outcome.')}</p> : null}
        <RawDisclosure label={text('任务标识', 'Task identifiers')}><code className="block break-all text-xs">{props.sid} / {props.view.mission.id || text('任务 ID 未记录', 'Task ID not recorded')}</code></RawDisclosure>
      </div>
    </Modal>
  </>;
}
