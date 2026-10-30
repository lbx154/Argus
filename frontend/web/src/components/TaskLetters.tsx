import type { BacklogItem } from '../api';
import { useI18n } from '../i18n';
import { MarkdownContent, WorkspaceImage } from './MarkdownContent';

/** The detail endpoint owns letters, the latest turn's edits and delivered paths. */
export function TaskLetters({ item, sid, onOpenArtifact }: {
  item: BacklogItem; sid: string | null; onOpenArtifact?: (path: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const paths = item.result_paths ?? [];
  const image = paths.find(path => /\.(png|jpe?g|gif|webp|svg)$/i.test(path));
  const files = (label: string, values: string[]) => <section>
    <h4 className="mb-1 text-xs font-medium text-ink-faint">{label}</h4>
    {values.length ? <ul className="space-y-1">{values.map(path => <li key={path}>
      <button type="button" className="max-w-full break-all text-left text-xs text-blue hover:underline" onClick={() => onOpenArtifact?.(path)}>{path}</button>
    </li>)}</ul> : <p className="text-xs text-ink-faint">{zh ? '暂无记录' : 'No record yet'}</p>}
  </section>;
  return <div data-task-letters className="min-w-0 space-y-3">
    {files(zh ? '本轮改动文件' : 'Files changed this turn', item.changed_files ?? [])}
    {!!paths.length && files(zh ? '结果文件' : 'Result files', paths)}
    {image && sid && <div data-task-thumbnail={image} className="max-w-full">
      <WorkspaceImage sid={sid} path={image} src={image} alt={image} className="max-h-48 max-w-full rounded object-contain"
        onOpen={onOpenArtifact ? () => onOpenArtifact(image) : undefined} />
    </div>}
    <section><h4 className="mb-1 text-xs font-medium text-ink-faint">{zh ? '往来信件' : 'Letters'}</h4>
      {item.notes ? <MarkdownContent sid={sid} artifacts={paths.map(path => ({ path }))} onOpenArtifact={onOpenArtifact}>{item.notes}</MarkdownContent>
        : <p className="text-xs text-ink-faint">{zh ? '暂无往来信件' : 'No letters yet'}</p>}
    </section>
  </div>;
}
