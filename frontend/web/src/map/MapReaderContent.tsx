import type { ArtifactInfo, ExplanationPhase } from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { useI18n } from '../i18n';
import { cleanDeliverySummary } from '../components/deliveryPresentation';
import { isReaderBrief, READER_BRIEF_VERSION } from '../research-brief/model';
import { ReaderExplanation, ReaderExplanationStatus } from '../research-brief/ReaderExplanation';
import type { MapTask } from './model';
import type { CardCopy, CardRequest } from './presentation';
import { PendingQuestion } from '../research-brief/PendingQuestion';
import { ProgressQuestionButton, type QuestionSourceContext } from '../research-brief/ProgressQuestions';

export interface MapReaderSelection {
  request: CardRequest;
  pending: boolean;
  generating: boolean;
  phase?: ExplanationPhase;
  error?: unknown;
  unavailable?: boolean;
  retry?: () => Promise<unknown>;
  retryDisabled?: boolean;
  foundationRequired?: boolean;
  questionContext?: QuestionSourceContext;
}

/** The reader holds the explanation and the way to ask about it; the records
 * it was written from are the map's own steps, one click away, not repeated here. */
export function MapReaderContent({ cardKey, taskId, card, task, originalDetail, selection, artifacts, onOpenArtifact, readOnly }: {
  cardKey: string; taskId: string; card?: CardCopy; originalDetail: string;
  task?: MapTask; selection?: MapReaderSelection; artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
  readOnly?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  if (task?.turn_kind === 'qa') {
    return <div className="macro-reader-markdown" data-reader-card={cardKey} data-reader-task-id={taskId}>
      <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{originalDetail}</MarkdownContent>
    </div>;
  }
  const selected = selection?.request.key === cardKey && selection.request.task_id === taskId ? selection : undefined;
  const brief = (card?.version ?? 0) >= READER_BRIEF_VERSION && isReaderBrief(card?.reader_brief) ? card!.reader_brief : undefined;
  return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
    <ReaderExplanationStatus generatedAt={card?.generated_at} pending={selected?.pending} generating={selected?.generating} hasExplanation={!!card}
      phase={selected?.phase} error={selected?.error} unavailable={selected?.unavailable} retry={selected?.retry} retryDisabled={selected?.retryDisabled} />
    {selected?.foundationRequired ? <p className="mt-2 text-xs text-ink-dim">{zh ? '先在“问题基础”里选择一份说明，再阅读本次进展的解释。' : 'Choose a saved foundation to explain this progress.'}</p> : null}
    {brief ? <ReaderExplanation brief={brief} identity={cardKey} detail={card?.detail} learningPath={card?.learning_path} foundation={card?.foundation_ref}
      readingUnavailable={card?.teaching_review?.reading_review?.status === 'unavailable'}
      teachingUnavailable={card?.teaching_review?.status === 'unavailable'} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />
      : <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{cleanDeliverySummary(card?.detail || originalDetail)}</MarkdownContent></div>}
    <PendingQuestion task={task?.id === taskId ? task : undefined} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />
    <div className="my-3"><ProgressQuestionButton card={card} cardKey={cardKey} taskId={taskId} readOnly={readOnly} context={selected?.questionContext} /></div>
  </div>;
}
