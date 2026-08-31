import { useEffect, useState } from 'react';
import { api } from '../api';
import { useArtifact } from '../hooks';
import { formatBytes } from '../lib/format';
import { HtmlPreview } from './HtmlPreview';
import { JsonPreview, TablePreview } from './DataPreview';
import { MarkdownContent } from './MarkdownContent';
import { Modal } from './Modal';
import { Spinner } from './primitives';
import { useI18n } from '../i18n';
import { PdfPreview } from './PdfPreview';
import { setDesktopLargePreview } from '../lib/desktopBridge';

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
  const { t } = useI18n();
  const artifactQ = useArtifact(sid, path);
  const info = artifactQ.data;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [pdfOrientation, setPdfOrientation] = useState<'portrait' | 'landscape'>('portrait');
  const pdfPreview = info?.kind === 'pdf' || path?.toLowerCase().endsWith('.pdf') === true;

  useEffect(() => {
    if (!path || !pdfPreview) return;
    setDesktopLargePreview(true);
    return () => setDesktopLargePreview(false);
  }, [path, pdfPreview]);

  useEffect(() => {
    setPdfOrientation('portrait');
    setPreviewUrl(null);
    setPreviewError('');
    if (!sid || !path || !info || !['image', 'pdf', 'audio', 'video'].includes(info.kind)) return;
    let alive = true;
    let objectUrl = '';
    const controller = new AbortController();
    api.artifactBlob(sid, path, false, controller.signal).then(
      (blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      },
      (error: Error) => alive && setPreviewError(error.message),
    );
    return () => {
      alive = false;
      controller.abort();
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
    <Modal
      open={Boolean(path)}
      onClose={onClose}
      label={t('artifact.preview')}
      width={pdfPreview ? 'max-w-none' : 'max-w-5xl'}
      viewport={pdfPreview}
      style={pdfPreview ? {
        maxWidth: pdfOrientation === 'portrait' ? 'min(96vw, 76dvh)' : 'min(96vw, 145dvh)',
      } : undefined}
    >
      <div className="flex shrink-0 items-start gap-3 border-b border-line px-4 py-3 sm:px-5">
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-mono text-sm font-semibold text-ink" title={info?.storage_path ?? info?.path ?? path ?? ''}>
            {info?.name ?? path ?? t('artifact.title')}
          </h2>
          <p className="mt-0.5 truncate text-[11px] text-ink-faint">
            {info ? `${info.kind} · ${formatBytes(info.size)} · ${info.mime}` : t('artifact.approvedEvidence')}
          </p>
        </div>
        <button
          type="button"
          disabled={!info || downloading}
          onClick={() => void download()}
          className="rounded-md border border-blue-deep/60 bg-blue-deep/10 px-3 py-1.5 text-xs text-blue-sky transition-colors hover:bg-blue-deep/20 disabled:cursor-wait disabled:opacity-50"
        >
          {downloading ? t('artifact.downloading') : t('artifact.download')}
        </button>
        <button
          type="button"
          aria-label={t('artifact.close')}
          onClick={onClose}
          className="rounded-md px-2 py-1 text-lg leading-none text-ink-faint hover:bg-surface hover:text-ink"
        >
          ×
        </button>
      </div>

      <div className={pdfPreview
        ? 'flex min-h-0 flex-1 flex-col overflow-hidden bg-bg/40'
        : 'flex min-h-64 max-h-[72vh] flex-col overflow-x-hidden overflow-y-auto bg-bg/40 p-3 scroll-thin sm:p-4'
      }>
        {artifactQ.isLoading ? <div className="m-auto"><Spinner /></div> : null}
        {artifactQ.isError ? (
          <div className="m-auto text-sm text-err">{t('artifact.unavailable')} · {(artifactQ.error as Error).message}</div>
        ) : null}
        {info?.why && !pdfPreview ? (
          <div className="mb-3 rounded-md border border-line bg-surface px-3 py-2 text-xs text-ink-dim">
            <span className="mr-1 text-ink-faint">Reviewer:</span>{info.why}
          </div>
        ) : null}
        {info?.kind === 'text' ? (
          <pre className="min-h-52 overflow-x-hidden overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-bg p-4 font-mono text-xs leading-relaxed text-ink-dim scroll-thin">
            {info.preview || t('artifact.empty')}
            {info.truncated ? `\n\n… ${t('artifact.truncated')}` : ''}
          </pre>
        ) : null}
        {info?.kind === 'markdown' ? (
          <div className="min-h-52 overflow-auto rounded-lg border border-line bg-bg p-4 text-sm text-ink-dim scroll-thin">
            <MarkdownContent>{info.preview || t('artifact.empty')}</MarkdownContent>
          </div>
        ) : null}
        {info?.kind === 'json' ? <JsonPreview value={info.preview || ''} /> : null}
        {info?.kind === 'table' ? (
          <TablePreview value={info.preview || ''} delimiter={info.name.endsWith('.tsv') ? '\t' : ','} />
        ) : null}
        {info?.kind === 'html' && !info.truncated ? (
          <div className="flex min-h-[60vh] overflow-hidden rounded-lg border border-line">
            <HtmlPreview html={info.preview || ''} title={`HTML preview: ${info.name}`} />
          </div>
        ) : null}
        {info?.kind === 'html' && info.truncated ? (
          <div className="m-auto text-sm text-warn">
            {t('artifact.htmlTooLarge')}
          </div>
        ) : null}
        {info?.kind === 'image' && previewUrl ? (
          <div className="flex min-h-64 flex-1 items-center justify-center rounded border border-line bg-bg/50">
            <img src={previewUrl} alt={info.why || info.name} className="max-h-[62vh] max-w-full object-contain" />
          </div>
        ) : null}
        {info?.kind === 'pdf' && previewUrl ? (
          <PdfPreview
            src={previewUrl}
            name={info.name}
            className="min-h-0 overflow-hidden"
            onPageOrientation={setPdfOrientation}
          />
        ) : null}
        {info?.kind === 'audio' && previewUrl ? (
          <div className="m-auto w-full max-w-xl">
            <audio controls preload="metadata" src={previewUrl} className="w-full" />
          </div>
        ) : null}
        {info?.kind === 'video' && previewUrl ? (
          <div className="flex min-h-64 flex-1 items-center justify-center rounded border border-line bg-black">
            <video controls playsInline preload="metadata" src={previewUrl} className="max-h-[62vh] max-w-full" />
          </div>
        ) : null}
        {info && ['image', 'pdf', 'audio', 'video'].includes(info.kind) && !previewUrl && !previewError ? (
          <div className="m-auto"><Spinner /></div>
        ) : null}
        {info?.kind === 'binary' ? (
          <div className="m-auto max-w-md text-center">
            <div className="text-3xl text-ink-faint">◇</div>
            <p className="mt-2 text-sm text-ink-dim">{t('artifact.noPreview')}</p>
            <p className="mt-1 text-xs text-ink-faint">{t('artifact.downloadHint')}</p>
          </div>
        ) : null}
        {previewError ? <div className="mt-3 text-center text-xs text-err">{previewError}</div> : null}
      </div>
    </Modal>
  );
}
