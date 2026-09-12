import type { DeliveryReceipt } from '../../../core/src/types';
import { cleanDeliverySummary, deliveryFiles } from './deliveryPresentation';
import { CheckCircle2, PackageCheck, Maximize2, Minimize2 } from 'lucide-react';
import './delivery.css';
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
import { isMarkdownArtifact } from '../lib/artifactPresentation';
import { downloadBlob } from '../lib/downloadBlob';

/** Authenticated preview/download for one result file the Reviewer has checked. */
export function ArtifactModal({
  sid,
  path,
  onClose,
  delivery,
  deliveries = [],
  onSelectDelivery,
  onSelectPath,
  reviewActivity,
}: {
  sid: string | null;
  path: string | null;
  onClose: () => void;
  delivery?: DeliveryReceipt | null;
  deliveries?: DeliveryReceipt[];
  onSelectDelivery?: (receipt: DeliveryReceipt) => void;
  onSelectPath?: (path: string) => void;
  reviewActivity?: 'revising' | 'reviewing';
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const files = delivery ? deliveryFiles(delivery) : [];
  const artifactQ = useArtifact(sid, path);
  const info = artifactQ.data;
  const markdownPreview = info ? isMarkdownArtifact(info) : false;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [pdfOrientation, setPdfOrientation] = useState<'portrait' | 'landscape'>('portrait');
  const pdfPreview = info?.kind === 'pdf' || path?.toLowerCase().endsWith('.pdf') === true;
  const reviewDocument = Boolean(path && /(?:^|[\\/])REVIEW\.md$/i.test(path));

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
  }, [sid, path, info?.kind, previewAttempt]);

  const download = async (bundle = false) => {
    if (!sid || !path || !info) return;
    setDownloading(true);
    setPreviewError('');
    try {
      const blob = bundle ? await api.artifactBundle(sid, path) : await api.artifactBlob(sid, path, true);
      downloadBlob(blob, bundle ? `${info.name.replace(/\.html?$/i, '')}-website.zip` : info.name);
    } catch (error) {
      setPreviewError((error as Error).message);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Modal
      open={Boolean(path || delivery)}
      onClose={onClose}
      label={delivery ? (zh ? '交付成果' : 'Delivery') : t('artifact.preview')}
      width={delivery ? expanded ? 'max-w-none' : 'max-w-6xl' : pdfPreview ? 'max-w-none' : 'max-w-5xl'}
      viewport={delivery ? expanded : pdfPreview}
      showClose={false}
      style={delivery ? { height: expanded ? '100dvh' : 'min(92dvh, 960px)', display: 'flex', flexDirection: 'column', overflow: 'hidden' } : pdfPreview ? {
        maxWidth: pdfOrientation === 'portrait' ? 'min(96vw, 76dvh)' : 'min(96vw, 145dvh)',
      } : undefined}
    >
      {delivery && !expanded && <header className="delivery-header">
        <div className="delivery-heading"><span className="delivery-mark"><PackageCheck size={22} /></span><div>
          <p>DELIVERY · {zh ? '交付成果' : 'Your results'}</p>
          <h2>{zh ? '成果文件' : 'Result files'}</h2>
        </div><button type="button" onClick={onClose} className="delivery-return" aria-label={zh ? '关闭交付弹窗' : 'Close delivery'}>{zh ? '返回地图' : 'Back to map'} ×</button></div>
        {deliveries.length > 1 ? <select aria-label={zh ? '选择交付任务' : 'Choose delivery'} className="delivery-task-select" value={delivery.delivery_id} onChange={(e) => { const receipt = deliveries.find((item) => item.delivery_id === e.target.value); if (receipt) onSelectDelivery?.(receipt); }}>
          {deliveries.map((receipt) => <option key={receipt.delivery_id} value={receipt.delivery_id}>{receipt.title}</option>)}
        </select> : <p className="delivery-task-title" title={delivery.title}>{delivery.title}</p>}
        <div className="delivery-facts"><span><CheckCircle2 size={13} />{['done', 'passed', 'approved', 'accepted'].includes(delivery.review_status) ? (zh ? '任务已完成' : 'Task completed') : (zh ? '可查看' : 'Available')}</span><span>{files.length} {zh ? '个文件' : 'files'}</span></div>
        {delivery.summary && <details className="delivery-summary"><summary>{zh ? '查看成果说明' : 'Result summary'}</summary><p>{cleanDeliverySummary(delivery.summary)}</p></details>}
      </header>}
      {!expanded && !!files.length && <nav className="delivery-files" aria-label={zh ? '交付文件' : 'Delivery files'}>{files.map((file) => <button type="button" key={file.path} aria-pressed={path === file.path} onClick={() => onSelectPath?.(file.path)} title={file.path}><span>{file.path.split('/').at(-1)}</span>{delivery?.primary_target?.path === file.path && <small>{zh ? '主要成果' : 'Main result'}</small>}</button>)}</nav>}
      <div className={`flex shrink-0 items-start gap-2 border-b border-line px-4 py-3 sm:px-5 ${!path ? "hidden" : ""}`}>
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-mono text-sm font-semibold text-ink" title={info?.storage_path ?? info?.path ?? path ?? ''}>
            {info?.name ?? path ?? t('artifact.title')}
          </h2>
          <p className="mt-0.5 truncate text-[11px] text-ink-faint">
            {info ? `${info.kind} · ${formatBytes(info.size)} · ${info.mime}` : t('artifact.approvedEvidence')}
          </p>
        </div>
        {delivery && <button type="button" onClick={() => setExpanded((value) => !value)} aria-label={expanded ? (zh ? '收起预览' : 'Exit full screen') : (zh ? '全屏预览' : 'Full screen preview')} title={zh ? '切换全屏预览' : 'Toggle full screen preview'} className="shrink-0 rounded-md border border-line p-2 text-ink-dim">{expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>}
        {info?.kind === 'html' && <button type="button" disabled={downloading} onClick={() => void download(true)} className="shrink-0 rounded-md border border-blue-deep/60 bg-blue-deep/10 px-3 py-2 text-xs text-blue-sky disabled:opacity-50">{zh ? '下载完整网页' : 'Download website'}</button>}
        <button
          type="button"
          disabled={!info || downloading}
          onClick={() => void download()}
          className="rounded-md border border-blue-deep/60 bg-blue-deep/10 px-3 py-1.5 text-xs text-blue-sky transition-colors hover:bg-blue-deep/20 disabled:cursor-wait disabled:opacity-50"
        >
          {downloading ? t('artifact.downloading') : t('artifact.download')}
        </button>
        {(!delivery || expanded) && <button
          type="button"
          aria-label={t('artifact.close')}
          onClick={onClose}
          className="rounded-md px-2 py-1 text-lg leading-none text-ink-faint hover:bg-surface hover:text-ink"
        >
          ×
        </button>}
      </div>

      <div className={delivery ? 'flex min-h-0 flex-1 flex-col overflow-auto bg-bg/40 p-2 sm:p-3' : pdfPreview
        ? 'flex min-h-0 flex-1 flex-col overflow-hidden bg-bg/40'
        : 'flex min-h-64 max-h-[72vh] flex-col overflow-x-hidden overflow-y-auto bg-bg/40 p-3 scroll-thin sm:p-4'
      }>
        {!path && delivery && <p className="m-auto p-6 text-sm text-ink-dim">{cleanDeliverySummary(delivery.summary)}</p>}
        {artifactQ.isLoading ? <div className="m-auto"><Spinner /></div> : null}
        {artifactQ.isError ? (
          <div className="m-auto text-sm text-err">{t('artifact.unavailable')} · {(artifactQ.error as Error).message}</div>
        ) : null}
        {info?.why && !pdfPreview && !delivery ? (
          <div className="mb-3 rounded-md border border-line bg-surface px-3 py-2 text-xs text-ink-dim">
            <span className="mr-1 text-ink-faint">Reviewer:</span>{info.why}
          </div>
        ) : null}
        {info?.kind === 'text' && !markdownPreview ? (
          <pre className="min-h-52 overflow-x-hidden overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-bg p-4 font-mono text-xs leading-relaxed text-ink-dim scroll-thin">
            {info.preview || t('artifact.empty')}
            {info.truncated ? `\n\n… ${t('artifact.truncated')}` : ''}
          </pre>
        ) : null}
        {info && markdownPreview ? (
          <div className="min-h-52 overflow-auto rounded-lg border border-line bg-bg p-4 text-sm text-ink-dim scroll-thin">
            {reviewDocument && <div className="mb-4 border-b border-line pb-3 text-xs leading-5 text-ink-faint" role="status">
              {reviewActivity === 'revising'
                ? (zh ? '正在根据这份意见修改论文，修改后的版本尚待复审。' : 'The paper is being revised against this opinion; the revised version awaits review.')
                : reviewActivity === 'reviewing'
                  ? (zh ? 'Reviewer 正在更新本轮审稿意见，内容会自动刷新。' : 'The Reviewer is updating this round’s opinion. This file refreshes automatically.')
                  : (zh ? '这是最近保存的审稿意见，文件更新后会自动刷新。' : 'This is the latest saved review. Changes to this file appear automatically.')}
              {info.mtime != null && <div>{zh ? '最近更新：' : 'Last updated: '}{new Date(info.mtime * 1000).toLocaleString(locale)}</div>}
            </div>}
            <MarkdownContent artifacts={files.map((file) => ({ path: file.path }))} onOpenArtifact={onSelectPath}>{info.preview || t('artifact.empty')}</MarkdownContent>
          </div>
        ) : null}
        {info?.kind === 'json' ? <JsonPreview value={info.preview || ''} /> : null}
        {info?.kind === 'table' ? (
          <TablePreview value={info.preview || ''} delimiter={info.name.endsWith('.tsv') ? '\t' : ','} />
        ) : null}
        {info?.kind === 'html' ? (
          <div className={`flex flex-1 overflow-hidden rounded-lg border border-line ${delivery ? 'min-h-0' : 'min-h-[60vh]'}`}>

            <HtmlPreview sid={sid} path={path} html={info.preview || ''} title={`HTML preview: ${info.name}`} />
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
            onRetry={() => setPreviewAttempt((value) => value + 1)}
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
