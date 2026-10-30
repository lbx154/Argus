import type { ArtifactInfo } from '../api';
import { useBacklogItem } from '../hooks';
import { ErrorMessage } from '../components/ErrorMessage';
import { ModalHeader } from '../components/Modal';
import { MarkdownContent } from '../components/MarkdownContent';
import { cleanDeliverySummary } from '../components/deliveryPresentation';
import { TaskTrace } from '../components/TaskTrace';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';
import { ACTIVE, type MapTask } from './model';
export function MapReaderContent({ cardKey, taskId, task, eventId, originalDetail, artifacts, onOpenArtifact, sessionId, workspace, zh = true }: {
  cardKey: string; taskId: string; originalDetail: string;
  eventId?: string; task?: MapTask; artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
  /** A live project's session: its team tasks show their recorded trajectory. */
  sessionId?: string; workspace?: string; zh?: boolean;
}) {
  if (sessionId && eventId) return <MapLetterReader {...{ cardKey, taskId, eventId, artifacts, onOpenArtifact, sessionId, workspace, zh }} />;
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
      <div className="macro-reader-markdown mb-4"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{shown(originalDetail)}</MarkdownContent></div>
      <TaskTrace sessionId={sessionId} taskId={taskId} zh={zh} running={ACTIVE.has(task.status)}
        workspace={workspace} conclusion={plainConclusion(task, zh)} />
    </div>;
  }
  return <div className="font-sans" data-reader-card={cardKey} data-reader-task-id={taskId}>
    <div className="macro-reader-markdown"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{shown(cleanDeliverySummary(originalDetail))}</MarkdownContent></div>
  </div>;
}

function MapLetterReader(props: { cardKey: string; taskId: string; eventId: string; artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void; sessionId: string; workspace?: string; zh: boolean }) {
  const detail = useBacklogItem(props.sessionId, props.taskId, props.eventId);
  return detail.isPending ? <p role="status">{props.zh ? '正在读取信件…' : 'Reading the letter…'}</p>
    : detail.isError ? <p role="alert"><ErrorMessage error={detail.error} locale={props.zh ? 'zh-CN' : 'en'} /></p>
    : <div className="macro-reader-markdown" data-reader-card={props.cardKey} data-reader-task-id={props.taskId}>
      <MarkdownContent artifacts={props.artifacts} onOpenArtifact={props.onOpenArtifact}>{relativizeWorkspacePaths(detail.data.notes || '', props.workspace)}</MarkdownContent>
    </div>;
}

/** The task's full latest words, or its status before progress has arrived. */
function plainConclusion(task: MapTask, zh: boolean): string {
  if (task.latest_action) return task.latest_action.text;
  if (ACTIVE.has(task.status)) return zh ? '还在进行中。' : 'Still in progress.';
  return task.title;
}

/** The map carries an excerpt; its reader asks for the original brief and progress. */
export function MapTaskReader({ task, sessionId, artifacts, onOpenArtifact, workspace, zh = true }: {
  task: MapTask; sessionId: string; artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void; workspace?: string; zh?: boolean;
}) {
  const detail = useBacklogItem(sessionId, task.id);
  return <>
    <ModalHeader title={task.turn_kind === 'qa' ? (zh ? '请求' : 'Request') : (zh ? '任务说明' : 'Task explanation')}
      sub={task.turn_kind === 'qa' ? detail.data?.objective || task.title : task.title} />
    <div className="px-6">
      {detail.isPending ? <p role="status">{zh ? '正在读取任务说明…' : 'Reading the task brief…'}</p>
        : detail.isError ? <p role="alert"><ErrorMessage error={detail.error} locale={zh ? 'zh-CN' : 'en'} /></p>
        : <MapReaderContent cardKey={task.id} taskId={task.id} task={{ ...task, latest_action: detail.data.latest_action }} sessionId={sessionId}
          originalDetail={task.turn_kind === 'qa' ? detail.data.latest_action?.text || (task.status === 'cancelled' ? (zh ? '本次回答已中断。' : 'This answer was interrupted.') : ACTIVE.has(task.status) ? (zh ? '正在回答…' : 'Answering…') : (zh ? '尚无回答。' : 'No answer recorded.')) : detail.data.objective || ''}
          artifacts={artifacts} onOpenArtifact={onOpenArtifact} workspace={workspace} zh={zh} />}
    </div>
  </>;
}
