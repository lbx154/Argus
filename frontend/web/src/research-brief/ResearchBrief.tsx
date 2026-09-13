import { useEffect, useRef, useState } from 'react';
import { BookOpen, MessageCircle, RefreshCw } from 'lucide-react';
import type { MissionView, Snapshot } from '../../../core/src/types';
import { Button, RawDisclosure } from '../components/primitives';
import { MarkdownContent } from '../components/MarkdownContent';
import { Modal, ModalHeader } from '../components/Modal';
import { useI18n } from '../i18n';
import { dateOf } from '../lib/format';
import { briefRequest, questionAboutStep } from './model';
import { useResearchBrief, type ResearchBriefOptions } from './useResearchBrief';
import { readerPreview } from '../map/copyMode';
import { MapReaderContent } from '../map/MapReaderContent';
import { ReaderExplanation, ReaderExplanationStatus, ShortText, readerExplanationBoundary } from './ReaderExplanation';
import { ReaderEvidence, ReaderTaskFacts } from './ReaderEvidence';
import { selectReaderEvidence } from './evidence';

export interface ResearchBriefProps {
  sid: string;
  snapshot: Snapshot;
  view: MissionView;
  active: boolean;
  readOnly?: boolean;
  compact?: boolean;
  onAsk?: (draft: string) => void;
  onOpenArtifact?: (path: string) => void;
}

type ReadingSelection = Pick<ResearchBriefOptions, 'sid' | 'snapshot' | 'view' | 'locale' | 'preview' | 'foundationId'>;

/** Observe the selected task's existing queries; opening a reader adds no work. */
function SelectedResearchReading({ selection, onOpenArtifact }: { selection: ReadingSelection; onOpenArtifact?: (path: string) => void }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const result = useResearchBrief({ ...selection, active: false, readOnly: true });
  const taskId = selection.view.mission.id;
  const task = result.task || { id: taskId, title: selection.view.mission.title,
    objective: selection.view.mission.objective, status: selection.view.mission.status };
  const title = (result.brief ? result.card?.title : undefined) || task.title;
  return <>
    <ModalHeader title={zh ? '读懂这一步' : 'Understand this step'} sub={title} />
    <div className="px-6 pb-6" data-testid="research-brief-reading" data-project-id={selection.sid} data-task-id={taskId}>
      <MapReaderContent cardKey={taskId} taskId={taskId} task={result.task} card={result.card}
        onOpenArtifact={onOpenArtifact}
        originalDetail={task.objective || selection.snapshot.session.objective || ''}
        selection={{ request: briefRequest(task, result.evidence), evidence: result.loadedEvents ?? [],
          pending: result.needsUpdate, generating: result.generating, phase: result.generationPhase,
          error: result.generationError, foundationRequired: result.foundationRequired,
          unavailable: !result.foundationRequired && (result.legacy || result.generationUnavailable || (!result.loading && !result.generationAvailable)) }} />
      {result.readError ? <p className="mt-2 text-xs text-ink-faint" role="status">{zh
        ? '记录暂时读取失败；已显示内容仍保留。'
        : 'Records could not be refreshed; previously loaded content is retained.'}</p> : null}
    </div>
  </>;
}

export default function ResearchBrief(props: ResearchBriefProps) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const text = (chinese: string, english: string) => zh ? chinese : english;
  const compact = props.compact === true;
  const [readingSelection, setReadingSelection] = useState<ReadingSelection | null>(null);
  const selectedReading = readingSelection?.sid === props.sid ? readingSelection : null;
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setEvidenceOpen(false);
    if (body.current) body.current.scrollTop = 0;
  }, [props.sid, props.view.mission.id]);
  useEffect(() => setReadingSelection(null), [props.sid]);
  const result = useResearchBrief({ ...props, locale: zh ? 'zh-CN' : 'en-US' });
  const { task, evidence, brief, card } = result;
  const title = (brief ? card?.title : undefined) || task?.title || props.view.mission.title || text('当前任务', 'Current task');
  const objective = task?.objective || props.view.mission.objective || props.snapshot.session.objective;
  const sources = selectReaderEvidence({ cardKey: props.view.mission.id, taskId: props.view.mission.id, card, task,
    loadedEvents: result.loadedEvents, currentEvents: evidence });
  const generatedDate = dateOf({ ts: card?.generated_at });
  const generatedAt = generatedDate?.toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) ?? '';
  const unavailable = !result.foundationRequired && (result.legacy || result.generationUnavailable || (!result.loading && !result.generationAvailable));
  const hasProblem = !!result.readError || !!result.generationError || unavailable;
  const explanationStatus = <ReaderExplanationStatus generatedAt={card?.generated_at} pending={result.needsUpdate} generating={result.generating} phase={result.generationPhase} hasExplanation={!!brief} />;

  const boundary = readerExplanationBoundary(zh);
  const explanation = <>
    {brief ? <ReaderExplanation brief={brief} identity={task?.id || props.view.mission.id} detail={card?.detail} learningPath={card?.learning_path} foundation={card?.foundation_ref} onOpenArtifact={props.onOpenArtifact} readingUnavailable={result.readingUnavailable} teachingUnavailable={result.teachingUnavailable} /> : <div className="mt-3 text-[13px] leading-6 text-ink-dim">
      <ShortText value={objective || text('任务目标尚未记录。', 'The task objective has not been recorded yet.')} expandLabel={text('完整任务目标', 'Full task objective')} />
      <p className="mt-1 text-xs text-ink-faint">{result.loading ? text('正在读取任务记录，原始目标会一直保留。', 'Loading the task records; the original objective remains visible.')
        : result.generating ? text('正在根据任务记录整理说明，可以先阅读原始目标。', 'Preparing an explanation from the task records; you can read the original objective meanwhile.')
        : result.foundationRequired ? text('先在“问题基础”里选择一份说明，再阅读本次进展的解释。', 'Choose a saved foundation to explain this progress.')
        : props.readOnly ? text('此处还没有阅读说明。只读模式可以查看已有记录，不会发起解释生成。', 'No explanation is available yet. Read-only mode shows existing records without generating new text.')
        : text('解说暂不可用，可以先查看任务目标与依据。', 'An explanation is not available yet. You can still view the task objective and evidence.')}</p>
    </div>}
    {result.generating && brief ? <p className="mt-2 text-[11px] text-ink-faint">{text('正在依据新记录更新；上方暂时保留之前的说明。', 'Updating from new records; the previous explanation remains visible above.')}{generatedAt ? ` ${generatedAt}` : ''}</p> : null}
    <ReaderTaskFacts selection={sources} onOpenArtifact={props.onOpenArtifact} />
    {hasProblem ? <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-faint" role="status"><span>{result.readError ? text('记录暂时读取失败；已显示内容仍保留。', 'Records could not be refreshed; previously loaded content is retained.')
      : result.generationError ? text('说明生成未完成；不会自动重复请求。', 'The explanation could not be prepared. This request will not be repeated automatically.')
        : text('解说服务暂不可用；任务记录不受影响。', 'Explanations are temporarily unavailable; the task records remain available.')}</span>
      {props.active && (!props.readOnly || result.readError) ? <Button className="inline-flex items-center gap-1 text-xs" disabled={result.generating || result.loading} onClick={() => void result.retry()}><RefreshCw size={12} />{text('重试', 'Retry')}</Button> : null}</div> : null}
  </>;

  return <><section className={`mx-4 flex min-h-0 flex-col overflow-hidden rounded-lg border border-line/70 bg-panel ${compact ? 'my-2 px-3 py-2' : 'my-3 max-h-[50vh] px-4 py-3'}`} aria-label={text('读懂这一步', 'Understand this step')} data-testid="research-brief" data-project-id={props.sid} data-task-id={props.view.mission.id} data-compact={compact}>
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
        <Button className="inline-flex items-center gap-1 text-xs" onClick={() => setReadingSelection({
          sid: props.sid, snapshot: props.snapshot, view: props.view, locale: zh ? 'zh-CN' : 'en-US', preview: readerPreview(), foundationId: result.foundationId,
        })}><BookOpen size={12} />{text('阅读说明', 'Read explanation')}</Button>
        <Button className="inline-flex items-center gap-1 text-xs" onClick={() => setEvidenceOpen(true)}><BookOpen size={12} />{text('查看依据', 'View evidence')}</Button>
        {props.onAsk && task && !props.readOnly ? <Button className="inline-flex items-center gap-1 text-xs" onClick={() => props.onAsk?.(questionAboutStep(props.sid, task, evidence, zh))}><MessageCircle size={12} />{compact && !zh ? <>Ask<span className="sr-only"> about latest progress</span></> : text('询问最新进展', 'Ask about latest progress')}</Button> : null}
      </div>
      {compact && result.generating ? <div className="mt-2">{explanationStatus}</div> : null}
      {!compact ? <p className="mt-1 text-[11px] text-ink-faint">{boundary}</p> : null}
    </footer>
  </section>
    <Modal open={!!selectedReading} onClose={() => setReadingSelection(null)} label={text('任务说明', 'Task explanation')}>
      {selectedReading ? <SelectedResearchReading selection={selectedReading} onOpenArtifact={props.onOpenArtifact} /> : null}
    </Modal>
    <Modal open={evidenceOpen} onClose={() => setEvidenceOpen(false)} label={text('这一步的依据', 'Evidence for this step')}>
      <ModalHeader title={text('这一步的依据', 'Evidence for this step')} sub={card?.title || task?.title || props.view.mission.title} />
      <div className="space-y-4 px-6 pb-6 text-[13px] leading-6 text-ink-dim">
        <ReaderEvidence selection={sources} />
        <RawDisclosure label={text('任务标识', 'Task identifiers')}><code className="block break-all text-xs">{props.sid} / {props.view.mission.id || text('任务 ID 未记录', 'Task ID not recorded')}</code></RawDisclosure>
      </div>
    </Modal>
  </>;
}
