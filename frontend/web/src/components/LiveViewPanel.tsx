import type { ArtifactInfo } from '../api';

export function LiveViewPanel({
  artifacts,
  error = false,
  onOpenArtifact,
  className = '',
}: {
  artifacts?: ArtifactInfo[];
  error?: boolean;
  onOpenArtifact: (path: string) => void;
  className?: string;
}) {
  const live = artifacts?.filter((item) => item.source === 'manager_live') ?? [];
  if (live.length === 0 && !error) return null;
  const title = live[0]?.group_title || 'Live project view';

  return (
    <section className={`card shrink-0 px-4 py-3 ${className}`} aria-label="Manager live project view">
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-sky">
        {title}
      </div>
      {live[0]?.why ? <p className="mt-1 text-xs leading-relaxed text-ink-dim">{live[0].why}</p> : null}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {live.map((item) => (
          <button
            key={item.path}
            type="button"
            disabled={!item.exists}
            onClick={() => item.exists && onOpenArtifact(item.path)}
            title={item.exists ? `Preview ${item.path}` : `${item.path} is not available yet`}
            className={`rounded border px-2 py-1 font-mono text-[10px] transition-colors ${
              item.exists
                ? 'border-blue-deep/50 bg-blue-deep/10 text-blue-sky hover:border-blue-sky/60 hover:bg-blue-deep/20'
                : 'cursor-not-allowed border-line/70 bg-bg/30 text-ink-faint'
            }`}
          >
            {item.path}{item.exists ? ' ↗' : ' · pending'}
          </button>
        ))}
      </div>
      {error ? <p className="mt-2 text-[10px] text-warn">Live preview unavailable.</p> : null}
    </section>
  );
}
