import type { ArtifactInfo } from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { cleanDeliverySummary } from '../components/deliveryPresentation';
import { isReaderBrief, READER_BRIEF_VERSION } from '../research-brief/model';
import { ReaderExplanation } from '../research-brief/ReaderExplanation';
import { TaskTrace } from '../components/TaskTrace';
import { ErrorMessage } from '../components/ErrorMessage';
import { errorText } from '../lib/format';
import { ACTIVE, type MapTask } from './model';
import type { CardCopy, CardRequest } from './presentation';
import { PendingQuestion } from '../research-brief/PendingQuestion';
import { ProgressQuestionButton, type QuestionSourceContext } from '../research-brief/ProgressQuestions';

export interface MapReaderSelection {
  request: CardRequest;
  questionContext?: QuestionSourceContext;
}

/** The reader holds a saved explanation, or the card's own record when none
 * was written, and the way to ask about it; the records an explanation was
 * written from are the map's own steps, one click away, not repeated here. */
export function MapReaderContent({ cardKey, taskId, card, task, originalDetail, selection, artifacts, onOpenArtifact, readOnly, sessionId, zh = true }: {
  cardKey: string; taskId: string; card?: CardCopy; originalDetail: string;
  task?: MapTask; selection?: MapReaderSelection; artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
  readOnly?: boolean;
  /** A live project's session: its team tasks show their recorded trajectory. */
  sessionId?: string; zh?: boolean;
}) {
  if (task?.turn_kind === 'qa') {
    return <div className="macro-reader-markdown" data-reader-card={cardKey} data-reader-task-id={taskId}>
      <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{originalDetail}</MarkdownContent>
    </div>;
  }
  if (sessionId && task && task.kind !== 'turn' && task.id === taskId) {
    // A team task reads as what it came to, then how: each role's reasoning,
    // words, file changes and tool calls, with commands folded.
    return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
      <PendingQuestion task={task} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />
      {task.status === 'failed' && task.last_error ? <ErrorMessage error={task.last_error} code="agent_failed" /> : null}
      <TaskTrace sessionId={sessionId} taskId={taskId} zh={zh} running={ACTIVE.has(task.status)}
        conclusion={task.status === 'failed' && task.last_error ? undefined : plainConclusion(task, zh)} />
      {card?.detail ? <details className="mt-3 text-sm"><summary>{zh ? '已保存的说明' : 'Saved explanation'}</summary>
        <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{cleanDeliverySummary(card.detail)}</MarkdownContent></div>
      </details> : null}
    </div>;
  }
  const selected = selection?.request.key === cardKey && selection.request.task_id === taskId ? selection : undefined;
  const brief = (card?.version ?? 0) >= READER_BRIEF_VERSION && isReaderBrief(card?.reader_brief) ? card!.reader_brief : undefined;
  return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
    {brief ? <ReaderExplanation brief={brief} identity={cardKey} detail={card?.detail} learningPath={card?.learning_path} foundation={card?.foundation_ref}
      readingUnavailable={card?.teaching_review?.reading_review?.status === 'unavailable'}
      teachingUnavailable={card?.teaching_review?.status === 'unavailable'} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />
      : <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{cleanDeliverySummary(card?.detail || originalDetail)}</MarkdownContent></div>}
    <PendingQuestion task={task?.id === taskId ? task : undefined} artifacts={artifacts} onOpenArtifact={onOpenArtifact} />
    {/* Questions are asked about a saved explanation; a bare record has none to ask about. */}
    {card ? <div className="my-3"><ProgressQuestionButton card={card} cardKey={cardKey} taskId={taskId} readOnly={readOnly} context={selected?.questionContext} /></div> : null}
  </div>;
}

/** Until a task delivers, its status is its conclusion, said plainly. */
function plainConclusion(task: MapTask, zh: boolean): string {
  if (task.status === 'superseded')
    return (zh ? '已被新计划替代' : 'Replaced by a new plan') + (task.superseded_reason ? (zh ? '：' : ': ') + task.superseded_reason : '');
  if (ACTIVE.has(task.status)) return zh ? '还在进行中。' : 'Still in progress.';
  if (task.status === 'failed') return errorText(task.last_error, zh ? 'zh-CN' : 'en', 'agent_failed');
  return task.title;
}
