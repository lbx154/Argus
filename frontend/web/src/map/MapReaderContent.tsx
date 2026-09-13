import type { ArtifactInfo, ExplanationPhase } from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { RawDisclosure } from '../components/primitives';
import { useI18n } from '../i18n';
import { cleanDeliverySummary } from '../components/deliveryPresentation';
import { isReaderBrief, READER_BRIEF_VERSION } from '../research-brief/model';
import { ReaderExplanation, ReaderExplanationStatus, readerExplanationBoundary } from '../research-brief/ReaderExplanation';
import type { MapEvent, MapTask } from './model';
import type { CardCopy, CardRequest } from './presentation';
import { ReaderEvidence, ReaderEvidenceSummary } from '../research-brief/ReaderEvidence';
import { selectReaderEvidence } from '../research-brief/evidence';

export interface MapReaderSelection {
  request: CardRequest;
  evidence: MapEvent[];
  pending: boolean;
  generating: boolean;
  phase?: ExplanationPhase;
  error?: unknown;
  unavailable?: boolean;
  retry?: () => Promise<unknown>;
  retryDisabled?: boolean;
  foundationRequired?: boolean;
}

/** Only this card's copy and event range enter the reader; no task-root fallback. */
export function MapReaderContent({ cardKey, taskId, card, task, originalDetail, selection, artifacts, onOpenArtifact }: {
  cardKey: string; taskId: string; card?: CardCopy; originalDetail: string;
  task?: MapTask; selection?: MapReaderSelection; artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const selected = selection?.request.key === cardKey && selection.request.task_id === taskId ? selection : undefined;
  const brief = (card?.version ?? 0) >= READER_BRIEF_VERSION && isReaderBrief(card?.reader_brief) ? card!.reader_brief : undefined;
  const sources = selectReaderEvidence({ cardKey, taskId, card, task, loadedEvents: selected?.evidence,
    currentEvents: selected?.evidence.filter(event => selected.request.event_ids.includes(event.id)) });
  const root = cardKey === taskId;
  return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
    <p className="text-xs text-ink-faint">{root
      ? zh ? '任务说明；来源按保存的材料核对。' : 'Task explanation with retained sources.'
      : zh ? '环节说明；来源按保存的材料核对。' : 'Step explanation with retained sources.'}</p>
    <ReaderExplanationStatus generatedAt={card?.generated_at} pending={selected?.pending} generating={selected?.generating} hasExplanation={!!card}
      phase={selected?.phase} error={selected?.error} unavailable={selected?.unavailable} retry={selected?.retry} retryDisabled={selected?.retryDisabled} />
    <ReaderEvidenceSummary selection={sources} />
    {selected?.foundationRequired ? <p className="mt-2 text-xs text-ink-dim">{zh ? '先在“问题基础”里选择一份说明，再阅读本次进展的解释。原始记录仍可查看。' : 'Choose a saved foundation to explain this progress. The original records remain available.'}</p> : null}
    {brief ? <ReaderExplanation brief={brief} identity={cardKey} detail={card?.detail} learningPath={card?.learning_path} foundation={card?.foundation_ref}
      readingUnavailable={card?.teaching_review?.reading_review?.status === 'unavailable'}
      teachingUnavailable={card?.teaching_review?.status === 'unavailable'} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />
      : <>
        <p className="my-2 text-xs text-ink-faint">{selected?.error || selected?.unavailable
          ? zh ? '可以先读已保留的详细记录。' : 'The retained detailed record is available below.'
          : zh ? '阅读说明待整理；可以先读已保留的详细记录。' : 'A reading explanation is pending. The retained detailed record is available below.'}</p>
        <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{cleanDeliverySummary(card?.detail || originalDetail)}</MarkdownContent></div>
      </>}
    <RawDisclosure label={zh ? '当前加载的任务与环节记录' : 'Currently loaded task and step record'}>
      <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{originalDetail}</MarkdownContent></div>
    </RawDisclosure>
    <RawDisclosure label={zh ? '查看依据' : 'View evidence'}>
      <ReaderEvidence selection={sources} showSummary={false} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />
    </RawDisclosure>
    <p className="mt-3 border-t border-line/50 pt-2 text-[11px] text-ink-faint">{readerExplanationBoundary(zh)}</p>
  </div>;
}
