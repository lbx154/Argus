import { useEffect, useMemo, useState } from 'react';
import type { ArtifactInfo } from '../api';
import { api } from '../api';
import { useArtifact } from '../hooks';
import { formatBytes } from '../lib/format';
import { Spinner } from './primitives';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faAnglesRight } from '@fortawesome/free-solid-svg-icons';

export function selectLiveArtifacts(artifacts?: ArtifactInfo[]): ArtifactInfo[] {
  return (artifacts ?? []).filter((item) => item.source === 'manager_live');
}

export function selectPreferredLiveArtifact(artifacts?: ArtifactInfo[]): ArtifactInfo | null {
  const live = selectLiveArtifacts(artifacts).filter((item) => item.exists);
  return live.find((item) => item.kind === 'pdf')
    ?? live.find((item) => item.kind === 'image')
    ?? live[0]
    ?? null;
}

function artifactLabel(item: ArtifactInfo): string {
  const parts = item.path.split('/');
  return parts[parts.length - 1] || item.path;
}

export function ResearchCanvas({
  sid,
  artifacts,
  error = false,
  onExpand,
  className = '',
  embedded = false,
  onCollapse,
}: {
  sid: string | null;
  artifacts?: ArtifactInfo[];
  error?: boolean;
  onExpand: (path: string) => void;
  className?: string;
  embedded?: boolean;
  onCollapse?: () => void;
}) {
  const live = useMemo(
    () => selectLiveArtifacts(artifacts),
    [artifacts],
  );
  const preferred = useMemo(() => selectPreferredLiveArtifact(artifacts), [artifacts]);
  const [manualPath, setManualPath] = useState<string | null>(null);

  useEffect(() => setManualPath(null), [sid]);

  const selected = live.find((item) => item.path === manualPath) ?? preferred;
  const artifactQ = useArtifact(sid, selected?.exists ? selected.path : null);
  const info = artifactQ.data;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');

  useEffect(() => {
    setPreviewUrl(null);
    setPreviewError('');
    if (!sid || !selected || !info || !['image', 'pdf'].includes(info.kind)) return;
    let alive = true;
    let objectUrl = '';
    const controller = new AbortController();
    api.artifactBlob(sid, selected.path, false, controller.signal).then(
      (blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      },
      (loadError: Error) => alive && setPreviewError(loadError.message),
    );
    return () => {
      alive = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sid, selected?.path, info?.kind, info?.mtime]);

  const title = live[0]?.group_title || 'Live research';
  const download = async () => {
    if (!sid || !selected) return;
    setDownloading(true);
    setDownloadError('');
    try {
      const blob = await api.artifactBlob(sid, selected.path, true);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = selected.name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (downloadError) {
      setDownloadError((downloadError as Error).message);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section className={`flex min-h-0 flex-col overflow-hidden bg-panel ${embedded ? '' : 'rounded-lg border border-line/80'} ${className}`} aria-label="Manager live research canvas">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line/50 bg-panel px-4">
        <div className="flex min-w-0 shrink-0 items-center gap-2">
          <span className="h-2 w-2 animate-pulse rounded-full bg-blue" />
          <h2 className="max-w-24 truncate text-sm font-semibold text-ink sm:max-w-48">{title}</h2>
        </div>
        {live.length > 0 ? (
          <label className="min-w-0 flex-1">
            <span className="sr-only">Preview artifact</span>
            <select
              value={selected?.path ?? ''}
              onChange={(event) => setManualPath(event.target.value)}
              className="h-8 w-full min-w-0 max-w-64 truncate rounded-md border border-line/50 bg-bg px-2 font-mono text-xs text-ink-dim outline-none focus:border-blue/60"
            >
              {!selected ? <option value="">Waiting…</option> : null}
              {live.map((item) => (
                <option key={item.path} value={item.path} disabled={!item.exists}>
                  {artifactLabel(item)}{item.exists ? '' : ' · pending'}
                </option>
              ))}
            </select>
          </label>
        ) : <div className="flex-1" />}
        <div className="shrink-0">
          {selected ? (
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={() => void download()}
                disabled={downloading || !selected.exists}
                title="Download artifact"
                aria-label="download artifact"
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-faint hover:bg-surface hover:text-ink disabled:opacity-40"
              >
                <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
                  <path d="M8 2.25v7.5M5.25 7.5 8 10.25 10.75 7.5M3 13.25h10" />
                </svg>
              </button>
              <button
                type="button"
                onClick={() => onExpand(selected.path)}
                title="Open large preview"
                aria-label="open large preview"
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-faint hover:bg-surface hover:text-ink"
              >
                <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
                  <path d="M6 3H3v3M10 3h3v3M6 13H3v-3M10 13h3v-3" />
                </svg>
              </button>
            </div>
          ) : null}
        </div>
        {onCollapse ? (
          <button type="button" onClick={onCollapse} aria-label="Collapse preview" title="Collapse preview" className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line/50 bg-bg/40 text-ink-faint hover:border-blue/50 hover:text-ink lg:flex">
            <FontAwesomeIcon icon={faAnglesRight} className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </header>

      <div key={selected?.path ?? 'empty'} className="relative flex min-h-0 flex-1 animate-appear flex-col bg-bg">
        {error ? (
          <div className="m-auto max-w-sm px-6 text-center text-sm text-warn">
            Manager live view is temporarily unavailable.
          </div>
        ) : null}
        {!error && live.length === 0 ? (
          <div className="m-auto max-w-sm px-8 text-center">
            <div className="text-3xl text-ink-faint">◇</div>
            <h3 className="mt-3 text-xs text-ink-faint">No preview</h3>
          </div>
        ) : null}
        {!error && live.length > 0 && !selected ? (
          <div className="m-auto max-w-sm px-8 text-center">
            <Spinner />
            <p className="mt-3 text-xs text-ink-faint">Waiting…</p>
          </div>
        ) : null}
        {selected && !selected.exists ? (
          <div className="m-auto max-w-sm px-8 text-center">
            <Spinner />
            <p className="mt-3 text-xs text-ink-faint">Updating…</p>
          </div>
        ) : null}
        {selected?.exists && artifactQ.isLoading ? <div className="m-auto"><Spinner /></div> : null}
        {selected?.exists && artifactQ.isError ? (
          <div className="m-auto px-6 text-center text-sm text-err">
            Preview unavailable · {(artifactQ.error as Error).message}
          </div>
        ) : null}
        {info?.kind === 'text' ? (
          <pre className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto whitespace-pre-wrap break-words p-5 font-mono text-xs leading-6 text-ink-dim scroll-thin">
            {info.preview || '(empty file)'}
            {info.truncated ? '\n\n… live preview truncated · expand to inspect the complete file' : ''}
          </pre>
        ) : null}
        {info?.kind === 'image' && previewUrl ? (
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-4">
            <img src={previewUrl} alt={info.why || info.name} className="max-h-full max-w-full object-contain" />
          </div>
        ) : null}
        {info?.kind === 'pdf' && previewUrl ? (
          <embed
            src={`${previewUrl}#toolbar=0&navpanes=0&scrollbar=0&view=FitH`}
            type="application/pdf"
            aria-label={`Live PDF preview: ${info.name}`}
            className="min-h-0 flex-1 bg-white"
          />
        ) : null}
        {info?.kind === 'binary' ? (
          <div className="m-auto max-w-sm px-8 text-center text-sm text-ink-dim">
            Preview unavailable for this file.
          </div>
        ) : null}
        {info && ['image', 'pdf'].includes(info.kind) && !previewUrl && !previewError ? (
          <div className="m-auto"><Spinner /></div>
        ) : null}
        {previewError ? <div className="m-auto px-6 text-center text-sm text-err">{previewError}</div> : null}
      </div>

      {info ? (
        <footer className="flex h-9 items-center gap-2 border-t border-line px-4 font-mono text-xs text-ink-faint">
          <span className="min-w-0 flex-1 truncate">{info.path}</span>
          {downloadError ? <span className="ml-auto truncate text-err" title={downloadError}>download failed</span> : null}
          <span className="shrink-0">{info.kind} · {formatBytes(info.size)}</span>
          <span className="shrink-0 text-ok">live</span>
        </footer>
      ) : null}
    </section>
  );
}
