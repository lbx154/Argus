import type { ArtifactInfo } from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { cleanDeliverySummary } from '../components/deliveryPresentation';
import { TaskTrace } from '../components/TaskTrace';
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
  if (sessionId && task && task.turn_kind !== 'qa' && task.id === taskId) {
    // A team task reads as what it came to, then how: each role's reasoning,
    // words, file changes and tool calls, with commands folded.
    return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
      <TaskTrace sessionId={sessionId} taskId={taskId} zh={zh} running={ACTIVE.has(task.status)}
        workspace={workspace} conclusion={plainConclusion(task, zh)} />
    </div>;
  }
  return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
    <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{shown(cleanDeliverySummary(originalDetail))}</MarkdownContent></div>
  </div>;
}

/** Until a task delivers, its status is its conclusion, said plainly. */
function plainConclusion(task: MapTask, zh: boolean): string {
  if (ACTIVE.has(task.status)) return zh ? '还在进行中。' : 'Still in progress.';
  return task.title;
}
