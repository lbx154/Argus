import { ErrorMessage } from './ErrorMessage';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ArtifactInfo, Snapshot } from '../api';
import type { DeliveryReceipt } from '../../../core/src/types';
import { api } from '../api';
import { useArtifact } from '../hooks';
import { formatBytes } from '../lib/format';
import { Spinner } from './primitives';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faAnglesRight } from '@fortawesome/free-solid-svg-icons';
import { HtmlPreview } from './HtmlPreview';
import { JsonPreview, TablePreview } from './DataPreview';
import { MarkdownContent } from './MarkdownContent';
import { useI18n } from '../i18n';
import { deliveryHeadingKey } from '../lib/deliveryKind';
import { PdfPreview } from './PdfPreview';
import { ModelPreview } from './ModelPreview';
import { modelFormat } from '../lib/modelFiles';
import { canEditArtifact, isMarkdownArtifact } from '../lib/artifactPresentation';
import { downloadBlob } from '../lib/downloadBlob';
import { WorkStatusBar } from './WorkStatusBar';
import { FileText } from 'lucide-react';
import { readLocalStorage, writeLocalStorage } from '../lib/storage';
import { deliveryFiles } from './deliveryPresentation';

export const FILES_PATH = '__argus_files__';

export function selectPreviewArtifacts(artifacts?: ArtifactInfo[]): ArtifactInfo[] {
  const all = artifacts ?? [];
  const kindPriority: Record<string, number> = {
    markdown: 0, pdf: 1, html: 2, text: 3, table: 4, json: 5,
    image: 6, video: 7, audio: 8, binary: 9,
  };
  const existingDelivery = all.filter((item) => item.source === 'delivery');
  const existingLive = all.filter((item) => item.source === 'manager_live');
  const existing = all.filter(
    (item) => item.source !== 'manager_live' && item.source !== 'delivery',
  );
  if (existing.length || existingDelivery.length) {
    return [
      ...existingDelivery,
      ...[...existing].sort(
        (left, right) => (kindPriority[left.kind] ?? 99) - (kindPriority[right.kind] ?? 99),
      ),
      ...existingLive,
    ];
  }
  return existingLive;
}

export function selectPreferredPreviewArtifact(artifacts?: ArtifactInfo[]): ArtifactInfo | null {
  return selectPreviewArtifacts(artifacts)[0] ?? null;
}

export function ResearchCanvas({
  sid,
  artifacts,
  error = false,
  onExpand,
  onOpenFile,
  className = '',
  embedded = false,
  onCollapse,
  requestedPath,
  requestedPathToken,
  snapshot,
  connected = true,
  filesFirst = false,
  onRevise,
  onEdit,
  onRetry,
  loading = false,
  latestDelivery,
  deliveriesLoaded,
  onOpenDelivery,
}: {
  sid: string | null;
  artifacts?: ArtifactInfo[];
  error?: boolean;
  onExpand: (path: string) => void;
  onOpenFile?: () => void;
  className?: string;
  embedded?: boolean;
  onCollapse?: () => void;
  requestedPath?: string | null;
  requestedPathToken?: number;
  snapshot?: Snapshot;
  connected?: boolean;
  filesFirst?: boolean;
  onRevise?: (path: string) => void;
  onEdit?: (path: string) => void;
  onRetry?: () => void;
  loading?: boolean;
  /** Newest delivered result, pinned above the file list so phones see it first. */
  latestDelivery?: DeliveryReceipt | null;
  /** This visit's snapshot, transcript and event window have all landed. */
  deliveriesLoaded: boolean;
  onOpenDelivery?: (delivery: DeliveryReceipt) => void;
}) {
  const { t, locale } = useI18n();
  const previewArtifacts = useMemo(
    () => selectPreviewArtifacts(artifacts),
    [artifacts],
  );
  const preferred = useMemo(() => selectPreferredPreviewArtifact(artifacts), [artifacts]);
  const [manualPath, setManualPath] = useState<string | null>(() => filesFirst && sid ? readLocalStorage(`argus.preview.file.v1:${sid}`) : null);
  const [saveFailed, setSaveFailed] = useState(false);
  const handledRequest = useRef<{ sid: string | null; path: string; token: number | undefined } | null>(null);

  useEffect(() => {
    if (filesFirst) setManualPath(sid ? readLocalStorage(`argus.preview.file.v1:${sid}`) : null);
  }, [sid, filesFirst]);
  useEffect(() => {
    if (!requestedPath) return;
    const previous = handledRequest.current;
    if (previous?.sid === sid && previous.path === requestedPath && previous.token === requestedPathToken) return;
    const target = previewArtifacts.find(
      (item) => item.path === requestedPath,
    );
    const path = filesFirst && requestedPath === FILES_PATH ? FILES_PATH : target?.path;
    if (!path) return;
    handledRequest.current = { sid, path, token: requestedPathToken };
    setManualPath(path);
    if (filesFirst && sid) setSaveFailed(!writeLocalStorage(`argus.preview.file.v1:${sid}`, path));
  }, [previewArtifacts, requestedPath, requestedPathToken, filesFirst, sid]);

  // A new delivery replaces what the panel showed before: the newest result
  // is what the person wants to see (the old chart must not linger). New means
  // delivered after the newest one known once the project loaded, both times
  // stamped by the server, so the browser clock plays no part.
  const followed = useRef<{ sid: string | null; since: number | null }>({ sid: null, since: null });
  const [newestPaths, setNewestPaths] = useState<string[]>([]);
  useEffect(() => {
    if (followed.current.sid !== sid) followed.current = { sid, since: null };
    const seen = followed.current;
    if (!deliveriesLoaded) return;
    const at = latestDelivery?.delivered_at ?? 0;
    if (seen.since === null) seen.since = at;
    if (!filesFirst || !latestDelivery || at <= seen.since) return;
    seen.since = at;
    setNewestPaths(deliveryFiles(latestDelivery).map((file) => file.path));
  }, [latestDelivery, deliveriesLoaded, sid, filesFirst]);
  useEffect(() => {
    if (!newestPaths.length) return;
    const target = newestPaths.find((path) => previewArtifacts.some((item) => item.path === path));
    if (!target) return;
    setNewestPaths([]);
    setManualPath(target);
    if (sid) setSaveFailed(!writeLocalStorage(`argus.preview.file.v1:${sid}`, target));
  }, [newestPaths, previewArtifacts, sid]);

  const selectPreviewPath = (path: string) => {
    setManualPath(path);
    if (filesFirst && sid) setSaveFailed(!writeLocalStorage(`argus.preview.file.v1:${sid}`, path));
    onOpenFile?.();
  };
  const effectivePath = manualPath ?? (filesFirst ? FILES_PATH : preferred?.path ?? '');
  const showFiles = effectivePath === FILES_PATH;
  const selected = showFiles
    ? null
    : previewArtifacts.find((item) => item.path === effectivePath)
      ?? (filesFirst ? null : preferred);
  const artifactQ = useArtifact(
    sid,
    selected?.path ?? null,
    selected?.mtime ?? null,
  );
  const info = artifactQ.data;
  const markdownPreview = info ? isMarkdownArtifact(info) : false;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  useEffect(() => {
    setPreviewUrl(null);
    setPreviewError(null);
    if (!sid || !selected || !info || !['image', 'pdf', 'audio', 'video'].includes(info.kind)) return;
    if (info.kind === 'pdf') {
      setPreviewUrl(api.artifactRawUrl(sid, selected.path));
      return;
    }
    let alive = true;
    let objectUrl = '';
    const controller = new AbortController();
    api.artifactBlob(sid, selected.path, false, controller.signal).then(
      (blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      },
      (loadError: Error) => alive && setPreviewError(loadError),
    );
    return () => {
      alive = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sid, selected?.path, info?.kind, info?.mtime]);

  const title = filesFirst ? (locale === 'zh-CN' ? '成果与文件' : 'Results & files')
    : previewArtifacts[0]?.group_title || t('research.artifact');
  const download = async () => {
    if (!sid || !selected) return;
    setDownloading(true);
    setDownloadError('');
    try {
      const blob = await api.artifactBlob(sid, selected.path, true, undefined);
      downloadBlob(blob, selected.name);
    } catch (downloadError) {
      setDownloadError((downloadError as Error).message);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section className={`glass-panel glass-panel--side flex min-h-0 flex-col overflow-hidden ${embedded ? '' : 'rounded-lg border'} ${className}`} aria-label={t('research.canvas')}>
      <header className="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b border-line/50 bg-panel px-3 py-2">
        {/* With a file's tools beside it the heading gives way, not the file's name. */}
        <div className="flex min-w-0 items-center gap-2">
          <span className={`h-2 w-2 shrink-0 rounded-full bg-ink-faint/60`} />
          <h2 className="max-w-24 truncate text-sm font-semibold text-ink sm:max-w-48">{title}</h2>
        </div>
        {filesFirst || previewArtifacts.length > 0 ? (
          <label className="min-w-0 flex-1 basis-28">
            <span className="sr-only">{t('research.previewArtifact')}</span>
            <select
              value={showFiles ? FILES_PATH : selected?.path ?? FILES_PATH}
              onChange={(event) => selectPreviewPath(event.target.value)}
              title={selected?.path}
              className="h-8 w-full min-w-0 max-w-64 truncate rounded-md border border-line/50 bg-bg px-2 font-mono text-xs text-ink-dim outline-none focus:border-blue/60"
            >
              {filesFirst && <option value={FILES_PATH}>{locale === 'zh-CN' ? '最近改过的文件' : 'Recently changed files'}</option>}
              {previewArtifacts.map((item) => (
                <option key={item.path} value={item.path} title={item.path}>
                  {item.source === 'delivery' ? `${t('research.optionDelivery')} · ` : ''}{item.path}
                </option>
              ))}
            </select>
          </label>
        ) : <div className="flex-1" />}
        <div className="shrink-0">
          {selected ? (
            <div className="flex shrink-0 items-center gap-1">
              {onEdit && info && canEditArtifact(info)
                && <button type="button" onClick={() => onEdit(selected.path)} className="rounded-md px-2 py-1 text-xs text-blue hover:bg-bg">{locale === 'zh-CN' ? '编辑与版本' : 'Edit & versions'}</button>}
              {onRevise && <button type="button" onClick={() => onRevise(selected.path)}
                className="rounded-md px-2 py-1 text-xs text-blue hover:bg-bg">{locale === 'zh-CN' ? '继续修改' : 'Revise'}</button>}
              <button
                type="button"
                onClick={() => void download()}
                disabled={downloading}
                title={t('artifact.download')}
                aria-label={t('artifact.download')}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ink-faint hover:bg-surface hover:text-ink disabled:opacity-40"
              >
                <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
                  <path d="M8 2.25v7.5M5.25 7.5 8 10.25 10.75 7.5M3 13.25h10" />
                </svg>
              </button>
              <button
                type="button"
                onClick={() => onExpand(selected.path)}
                title={t('research.openLarge')}
                aria-label={t('research.openLarge')}
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
          <button type="button" onClick={onCollapse} aria-label={t('research.collapse')} title={t('research.collapse')} className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line/50 bg-bg/40 text-ink-faint hover:border-blue/50 hover:text-ink lg:flex">
            <FontAwesomeIcon icon={faAnglesRight} className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </header>

      {filesFirst && latestDelivery ? (
        <button type="button" data-latest-result onClick={() => onOpenDelivery?.(latestDelivery)} disabled={!onOpenDelivery}
          className="mx-3 mt-3 flex shrink-0 items-center gap-3 rounded-lg border border-ok/35 bg-ok/5 px-3 py-2.5 text-left hover:border-ok/60 disabled:cursor-default">
          <span className="min-w-0 flex-1">
            <span className="block text-[10px] font-semibold uppercase tracking-[0.12em] text-ok">{locale === 'zh-CN' ? '最新成果' : 'Latest result'} · {t(deliveryHeadingKey(latestDelivery))}</span>
            <span className="mt-0.5 block truncate text-sm font-medium text-ink" title={latestDelivery.title}>{latestDelivery.title}</span>
          </span>
          {onOpenDelivery ? <span className="shrink-0 text-xs font-medium text-ok">{locale === 'zh-CN' ? '打开' : 'Open'}</span> : null}
        </button>
      ) : null}
      {!filesFirst && snapshot ? <WorkStatusBar snapshot={snapshot} connected={connected} compact /> : null}

      <div className="relative flex min-h-0 flex-1 flex-col bg-bg">
        {saveFailed && <p role="status" className="p-3 text-xs text-warn">{locale === 'zh-CN' ? '浏览器未能保存阅读位置；刷新后请重新选择文件。' : 'This browser could not save your reading selection. Choose the file again after reloading.'}</p>}
        {showFiles && <div className="file-browser">
          {loading && <p role="status" className="text-sm text-ink-faint">{t('common.loading')}</p>}
          {/* A file is named with the folder it is in: a repository holds many an index.js. */}
          {previewArtifacts.map(file => <button type="button" key={file.path} onClick={() => selectPreviewPath(file.path)}>
            <FileText size={20} className="shrink-0 text-blue" aria-hidden="true" />
            <span className="min-w-0"><strong>{file.name}</strong><small>{file.path.includes('/') ? `${file.path.slice(0, file.path.lastIndexOf('/'))} · ` : ''}{formatBytes(file.size)}{file.source === 'delivery' ? (locale === 'zh-CN' ? ' · 交付文件' : ' · Delivered file') : ''}</small></span>
          </button>)}
        </div>}
        {error ? (
          <div className="m-auto max-w-sm px-6 text-center text-sm text-warn">
            {t('research.unavailable')}
            {onRetry && <button type="button" onClick={onRetry} className="ml-2 text-blue underline">{t('common.retry')}</button>}
          </div>
        ) : null}
        {!loading && !error && previewArtifacts.length === 0 ? (
          <div className="m-auto max-w-sm px-8 text-center">
            <div className="text-3xl text-ink-faint">◇</div>
            <h3 className="mt-3 text-xs text-ink-faint">{t('research.noPreview')}</h3>
          </div>
        ) : null}
        {!showFiles && !error && previewArtifacts.length > 0 && !selected ? (
          <div className="m-auto max-w-sm px-8 text-center">
            {filesFirst ? <><p className="text-sm text-ink-dim">{locale === 'zh-CN' ? '上次查看的文件不在当前列表中。' : 'The previously opened file is no longer listed.'}</p><button type="button" className="mt-3 text-sm text-blue" onClick={() => selectPreviewPath(FILES_PATH)}>{locale === 'zh-CN' ? '查看最近改过的文件' : 'See recently changed files'}</button></>
              : <><Spinner /><p className="mt-3 text-xs text-ink-faint">{t('research.waiting')}</p></>}
          </div>
        ) : null}
        {selected && artifactQ.isLoading ? <div className="m-auto"><Spinner /></div> : null}
        {selected && artifactQ.isError ? (
          <div className="m-auto px-6 text-center text-sm text-err">
            {t('artifact.unavailable')} · <ErrorMessage error={artifactQ.error} />
          </div>
        ) : null}
        {info?.kind === 'text' && !markdownPreview ? (
          <pre className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto whitespace-pre-wrap break-words p-5 font-mono text-xs leading-6 text-ink-dim scroll-thin">
            {info.preview || '(empty file)'}
            {info.truncated ? '\n\n… live preview truncated · expand to inspect the complete file' : ''}
          </pre>
        ) : null}
        {info && markdownPreview ? (
          <div className="min-h-0 flex-1 overflow-auto p-5 text-sm text-ink-dim scroll-thin">
            <MarkdownContent sid={sid} basePath={info.path} artifacts={artifacts} onOpenArtifact={selectPreviewPath}>{info.preview || '(empty file)'}</MarkdownContent>
          </div>
        ) : null}
        {info?.kind === 'json' ? <JsonPreview value={info.preview || ''} /> : null}
        {info?.kind === 'table' ? (
          <TablePreview value={info.preview || ''} delimiter={info.name.endsWith('.tsv') ? '\t' : ','} />
        ) : null}
        {info?.kind === 'html' && !info.truncated ? (
          <HtmlPreview sid={sid} path={info.path} html={info.preview || ''} title={`Live HTML preview: ${info.name}`} />
        ) : null}
        {info?.kind === 'html' && info.truncated ? (
          <div className="m-auto max-w-sm px-8 text-center text-sm text-warn">
            {t('artifact.htmlTooLarge')}
          </div>
        ) : null}
        {info?.kind === 'image' && previewUrl ? (
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-4">
            <img src={previewUrl} alt={info.name} className="max-h-full max-w-full object-contain" />
          </div>
        ) : null}
        {info?.kind === 'pdf' && previewUrl ? (
          <PdfPreview src={previewUrl} name={info.name} />
        ) : null}
        {info?.kind === 'audio' && previewUrl ? (
          <div className="m-auto w-full max-w-xl px-6">
            <audio controls preload="metadata" src={previewUrl} className="w-full" />
          </div>
        ) : null}
        {info?.kind === 'video' && previewUrl ? (
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-black p-2">
            <video controls playsInline preload="metadata" src={previewUrl} className="max-h-full max-w-full" />
          </div>
        ) : null}
        {info && sid && (info.kind === 'model' || modelFormat(info.path)) ? (
          <div className="flex min-h-0 flex-1 flex-col p-2">
            <ModelPreview key={info.path} sid={sid} path={info.path} constraintFiles={info.constraint_files} />
          </div>
        ) : null}
        {info?.kind === 'binary' && !modelFormat(info.path) ? (
          <div className="m-auto max-w-sm px-8 text-center text-sm text-ink-dim">
            {t('research.fileUnavailable')}
          </div>
        ) : null}
        {info && ['image', 'pdf', 'audio', 'video'].includes(info.kind) && !previewUrl && !previewError ? (
          <div className="m-auto"><Spinner /></div>
        ) : null}
        {previewError ? <div className="m-auto px-6 text-center text-sm text-err"><ErrorMessage error={previewError} /></div> : null}
      </div>

      {info ? (
        <footer className="flex h-9 items-center gap-2 border-t border-line px-4 font-mono text-xs text-ink-faint">
          <span className="min-w-0 flex-1 truncate" title={info.path}>{info.path}</span>
          {downloadError ? <span className="ml-auto truncate text-err" title={t('research.downloadFailed')}>{t('research.downloadFailed')}</span> : null}
          <span className="shrink-0">{formatBytes(info.size)}</span>
        </footer>
      ) : null}
    </section>
  );
}
