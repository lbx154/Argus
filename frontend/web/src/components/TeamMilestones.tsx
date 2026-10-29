import { useState } from 'react';
import { useI18n } from '../i18n';
import { clockOf } from '../lib/format';
import { isImageFile, type MilestoneLine } from '../lib/milestones';
import { WorkspaceImage } from './MarkdownContent';

/** Past this many milestones in one stretch, the older ones fold behind one control. */
export const MILESTONES_SHOWN = 3;
const FOLD_AFTER = 4;
const THUMBNAILS = 3;

/**
 * What the team got done between two messages, one recorded milestone per
 * line: a round, a review's verdict with its reason, files and figures it
 * wrote, a hold. A long stretch folds its older lines, except the latest
 * verdict and what stopped the work.
 */
export function TeamMilestones({ lines, sid, onOpenArtifact }: {
  lines: MilestoneLine[];
  sid: string | null;
  onOpenArtifact?: (path: string) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [open, setOpen] = useState(false);
  // Folded, the newest lines stay, and so do the latest verdict and anything that stopped the work.
  const lastReview = lines.map(line => line.kind).lastIndexOf('review');
  const shown = open || lines.length <= FOLD_AFTER ? lines : lines.filter((line, index) =>
    index >= lines.length - MILESTONES_SHOWN || index === lastReview || line.kind === 'stopped');
  const folded = lines.length - shown.length;
  return (
    <section data-team-milestones={lines.length} className="team-milestones mx-auto w-full max-w-full px-4 py-1 sm:px-6 lg:max-w-[808px]"
      aria-label={zh ? '团队进展' : 'Team progress'}>
      {folded ? <button type="button" data-milestones-folded={folded} onClick={() => setOpen(true)}
        className="team-milestones-fold">
        {zh ? `较早的 ${folded} 条进展已折叠 · 展开` : `${folded} earlier updates folded · Show`}
      </button> : null}
      <ol>
        {shown.map(line => {
          // Only a file the workspace serves shows as a picture; one under a dot folder stays a name.
          const figures = sid ? line.files.filter(path => isImageFile(path) && !path.split('/').some(part => part.startsWith('.')))
            .slice(-THUMBNAILS) : [];
          return <li key={line.id} data-milestone={line.kind} className="team-milestone">
            <span className="status-dot" data-tone={line.tone} aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 text-ink-dim" title={line.files.join('\n') || undefined}>{line.text}</span>
                <time className="shrink-0 font-mono text-[11px] tabular-nums text-ink-faint">{clockOf({ ts: line.ts })}</time>
              </div>
              {line.detail ? <p data-milestone-detail className="mt-0.5 line-clamp-3 text-xs leading-5 text-ink-faint" title={line.detail}>{line.detail}</p> : null}
              {figures.length ? <div className="mt-1.5 flex flex-wrap gap-2">
                {figures.map(path => <div key={path} data-milestone-figure={path} className="team-milestone-figure">
                  <WorkspaceImage sid={sid} path={path} src={path} alt={path.split('/').pop()}
                    className="max-h-40 w-auto max-w-full rounded-md object-contain"
                    onOpen={onOpenArtifact ? () => onOpenArtifact(path) : undefined} />
                </div>)}
              </div> : null}
            </div>
          </li>;
        })}
      </ol>
    </section>
  );
}
