import type { ArtifactInfo } from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { cleanDeliverySummary } from '../components/deliveryPresentation';
import { TaskTrace } from '../components/TaskTrace';
import { ErrorMessage } from '../components/ErrorMessage';
import { errorText } from '../lib/format';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';
import { ACTIVE, type MapTask } from './model';
export function MapReaderContent({ cardKey, taskId, task, originalDetail, artifacts, onOpenArtifact, sessionId, workspace, zh = true }: {
  cardKey: string; taskId: string; originalDetail: string;
  task?: MapTask; artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
  /** A live project's session: its team tasks show their recorded trajectory. */
  sessionId?: string; workspace?: string; zh?: boolean;
}) {
  const shown = (text: string) => relativizeWorkspacePaths(text, workspace);
  if (task?.turn_kind === 'qa') {
    return <div className="macro-reader-markdown" data-reader-card={cardKey} data-reader-task-id={taskId}>
      <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{shown(originalDetail)}</MarkdownContent>
    </div>;
  }
  if (sessionId && task && task.kind !== 'turn' && task.id === taskId) {
    // A team task reads as what it came to, then how: each role's reasoning,
    // words, file changes and tool calls, with commands folded.
    return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
      <PendingQuestion task={task} zh={zh} artifacts={artifacts} onOpenArtifact={onOpenArtifact} workspace={workspace} />
      {task.status === 'failed' && task.last_error ? <ErrorMessage error={task.last_error} code="agent_failed" /> : null}
      <TaskTrace sessionId={sessionId} taskId={taskId} zh={zh} running={ACTIVE.has(task.status)}
        workspace={workspace} conclusion={task.status === 'failed' && task.last_error ? undefined : plainConclusion(task, zh)} />
    </div>;
  }
  return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
    <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{shown(cleanDeliverySummary(originalDetail))}</MarkdownContent></div>
    <PendingQuestion task={task?.id === taskId ? task : undefined} zh={zh} artifacts={artifacts} onOpenArtifact={onOpenArtifact} workspace={workspace} />
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

/** A question the work is waiting on is the one thing a reader must not miss. */
function PendingQuestion({ task, artifacts, onOpenArtifact, zh, workspace }: {
  task?: Pick<MapTask, 'id' | 'pending_question'>; zh: boolean; artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void; workspace?: string;
}) {
  const question = typeof task?.pending_question === 'string' ? task.pending_question.trim() : '';
  if (!question) return null;
  return <section className="mt-4 min-w-0 border-t border-line/60 pt-3 text-[13px] leading-6 text-ink-dim" data-reader-pending-question={task!.id}>
    <h3 className="text-xs font-medium text-ink">{zh ? '这一步在等你回答' : 'This step is waiting for your answer'}</h3>
    <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{relativizeWorkspacePaths(question, workspace)}</MarkdownContent>
  </section>;
}
