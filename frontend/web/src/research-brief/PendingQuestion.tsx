import type { ArtifactInfo } from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { useI18n } from '../i18n';
import type { MapTask } from '../map/model';

/** A question the work is waiting on is the one thing a reader must not miss. */
export function PendingQuestion({ task, artifacts, onOpenArtifact }: {
  task?: Pick<MapTask, 'id' | 'pending_question'>; artifacts?: ArtifactInfo[]; onOpenArtifact?: (path: string) => void;
}) {
  const zh = useI18n().locale === 'zh-CN';
  const question = typeof task?.pending_question === 'string' ? task.pending_question.trim() : '';
  if (!question) return null;
  return <section className="mt-4 min-w-0 border-t border-line/60 pt-3 text-[13px] leading-6 text-ink-dim" data-reader-pending-question={task!.id}>
    <h3 className="text-xs font-medium text-ink">{zh ? '这一步在等你回答' : 'This step is waiting for your answer'}</h3>
    <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{question}</MarkdownContent>
  </section>;
}
