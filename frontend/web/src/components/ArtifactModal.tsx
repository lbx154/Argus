import { useEffect, useState } from 'react';
import { api } from '../api';
import { useArtifact } from '../hooks';
import { formatBytes } from '../lib/format';
import { Modal } from './Modal';
import { Spinner } from './primitives';

/** Authenticated preview/download for one reviewer-approved result file. */
export function ArtifactModal({
  sid,
  path,
  onClose,
}: {
  sid: string | null;
  path: string | null;
  onClose: () => void;
}) {
  const artifactQ = useArtifact(sid, path);
  const info = artifactQ.data;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    setPreviewUrl(null);
    setPreviewError('');
    if (!sid || !path || !info || !['image', 'pdf'].includes(info.kind)) return;
    let alive = true;
    let objectUrl = '';
    api.artifactBlob(sid, path).then(
      (blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      },
      (error: Error) => alive && setPreviewError(error.message),
    );
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sid, path, info?.kind]);

  const download = async () => {
    if (!sid || !path || !info) return;
    setDownloading(true);
    setPreviewError('');
    try {
      const blob = await api.artifactBlob(sid, path, true);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = info.name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      setPreviewError((error as Error).message);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Modal open={Boolean(path)} onClose={onClose} label="Artifact preview" width="max-w-5xl">
      <div className="flex items-start gap-3 border-b border-line px-4 py-3 sm:px-5">
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-mono text-sm font-semibold text-ink" title={info?.path ?? path ?? ''}>
            {info?.name ?? path ?? 'Artifact'}
          </h2>
          <p className="mt-0.5 truncate text-[11px] text-ink-faint">
            {info ? `${info.kind} · ${formatBytes(info.size)} · ${info.mime}` : 'reviewer-approved evidence'}
          </p>
        </div>
        <button
          type="button"
          disabled={!info || downloading}
          onClick={() => void download()}
          className="rounded-md border border-blue-deep/60 bg-blue-deep/10 px-3 py-1.5 text-xs text-blue-sky transition-colors hover:bg-blue-deep/20 disabled:cursor-wait disabled:opacity-50"
        >
          {downloading ? 'Downloading…' : 'Download'}
        </button>
        <button
          type="button"
          aria-label="close artifact preview"
          onClick={onClose}
          className="rounded-md px-2 py-1 text-lg leading-none text-ink-faint hover:bg-surface hover:text-ink"
        >
          ×
        </button>
      </div>

      <div className="flex min-h-64 max-h-[72vh] flex-col overflow-auto bg-bg/40 p-3 scroll-thin sm:p-4">
        {artifactQ.isLoading ? <div className="m-auto"><Spinner /></div> : null}
        {artifactQ.isError ? (
          <div className="m-auto text-sm text-err">preview unavailable · {(artifactQ.error as Error).message}</div>
        ) : null}
        {info?.why ? (
          <div className="mb-3 rounded-md border border-line bg-surface px-3 py-2 text-xs text-ink-dim">
            <span className="mr-1 text-ink-faint">Reviewer:</span>{info.why}
          </div>
        ) : null}
        {info?.kind === 'text' ? (
          <pre className="min-h-52 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-bg p-4 font-mono text-xs leading-relaxed text-ink-dim scroll-thin">
            {info.preview || '(empty file)'}
            {info.truncated ? '\n\n… preview truncated · download to inspect the complete file' : ''}
          </pre>
        ) : null}
        {info?.kind === 'image' && previewUrl ? (
          <div className="flex min-h-64 flex-1 items-center justify-center rounded border border-line bg-bg/50">
            <img src={previewUrl} alt={info.why || info.name} className="max-h-[62vh] max-w-full object-contain" />
          </div>
        ) : null}
        {info?.kind === 'pdf' && previewUrl ? (
          <iframe
            src={previewUrl}
            title={`PDF preview: ${info.name}`}
            sandbox=""
            referrerPolicy="no-referrer"
            className="h-[62vh] w-full rounded-lg border border-line bg-white"
          />
        ) : null}
        {info && ['image', 'pdf'].includes(info.kind) && !previewUrl && !previewError ? (
          <div className="m-auto"><Spinner /></div>
        ) : null}
        {info?.kind === 'binary' ? (
          <div className="m-auto max-w-md text-center">
            <div className="text-3xl text-ink-faint">◇</div>
            <p className="mt-2 text-sm text-ink-dim">This file type has no safe inline preview.</p>
            <p className="mt-1 text-xs text-ink-faint">Download it to inspect with a local application.</p>
          </div>
        ) : null}
        {previewError ? <div className="mt-3 text-center text-xs text-err">{previewError}</div> : null}
      </div>
    </Modal>
  );
}
