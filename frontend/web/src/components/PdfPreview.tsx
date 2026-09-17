import { useEffect, useRef, useState } from 'react';
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  RenderTask,
} from 'pdfjs-dist';
import { useI18n } from '../i18n';
import { loadPdfEngine, resetPdfEngine } from '../lib/pdfEngine';
import { Spinner } from './primitives';

export function pdfContainScale(
  pageWidth: number,
  pageHeight: number,
  viewportWidth: number,
  viewportHeight: number,
): number {
  const widthScale = Math.max(1, viewportWidth - 32) / Math.max(1, pageWidth);
  const heightScale = Math.max(1, viewportHeight - 32) / Math.max(1, pageHeight);
  return Math.min(2.5, widthScale, heightScale);
}

/**
 * Plugin-free PDF renderer for the sandboxed Desktop cockpit.
 *
 * WebView2 blocks its built-in PDF extension inside our intentionally sandboxed
 * iframe. PDF.js renders authenticated bytes to a canvas instead, preserving
 * the iframe security boundary and working identically in browser and Desktop.
 */
export function PdfPreview({
  src,
  name,
  className = '',
  onPageOrientation,
  onRetry,
}: {
  src: string;
  name: string;
  className?: string;
  onPageOrientation?: (orientation: 'portrait' | 'landscape') => void;
  onRetry?: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const zoomAnchorRef = useRef<{ x: number; y: number } | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const update = () => setViewportSize({
      width: element.clientWidth,
      height: element.clientHeight,
    });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    let task: PDFDocumentLoadingTask | null = null;
    setPdf(null);
    setPageNumber(1);
    setZoom(1);
    zoomAnchorRef.current = null;
    viewportRef.current?.scrollTo(0, 0);
    setError('');
    setLoading(true);

    void Promise.all([
      fetch(src, { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error(`PDF request failed (${response.status})`);
        return response.arrayBuffer();
      }),
      loadPdfEngine(),
    ]).then(async ([bytes, pdfjs]) => {
      if (!alive) return;
      task = pdfjs.getDocument({ data: new Uint8Array(bytes) });
      const loaded = await task.promise;
      if (!alive) return;
      setPdf(loaded);
      setLoading(false);
    }).catch((caught: unknown) => {
      if (!alive) return;
      setLoading(false);
      setError(caught instanceof Error ? caught.message : String(caught));
    });

    return () => {
      alive = false;
      controller.abort();
      void task?.destroy().catch(() => {});
    };
  }, [src, loadAttempt]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!pdf || !canvas || viewportSize.width <= 0 || viewportSize.height <= 0) return;
    let cancelled = false;
    let renderTask: RenderTask | null = null;
    setRendering(true);
    setError('');

    void pdf.getPage(pageNumber).then((page) => {
      if (cancelled || !canvasRef.current) return;
      const natural = page.getViewport({ scale: 1 });
      onPageOrientation?.(natural.width > natural.height ? 'landscape' : 'portrait');
      // Contain at 100%: portrait pages use the full available height, while
      // landscape pages expand proportionally without being cropped.
      const fitScale = pdfContainScale(
        natural.width,
        natural.height,
        viewportSize.width,
        viewportSize.height,
      );
      const viewport = page.getViewport({ scale: fitScale * zoom });
      // Render offscreen, then swap the completed pixels. Resizing the visible
      // canvas first clears it and makes zoom/side-panel transitions flash.
      const buffer = document.createElement('canvas');
      const context = buffer.getContext('2d', { alpha: false });
      if (!context) throw new Error('Canvas rendering is unavailable');
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      buffer.width = Math.max(1, Math.floor(viewport.width * pixelRatio));
      buffer.height = Math.max(1, Math.floor(viewport.height * pixelRatio));
      renderTask = page.render({
        canvas: buffer,
        canvasContext: context,
        viewport,
        transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
      });
      return renderTask.promise.then(() => {
        if (cancelled || !canvasRef.current) return;
        const target = canvasRef.current;
        const visibleContext = target.getContext('2d', { alpha: false });
        if (!visibleContext) throw new Error('Canvas rendering is unavailable');
        target.width = buffer.width;
        target.height = buffer.height;
        target.style.width = `${viewport.width}px`;
        target.style.height = `${viewport.height}px`;
        visibleContext.drawImage(buffer, 0, 0);
        const scroller = viewportRef.current;
        const anchor = zoomAnchorRef.current;
        if (scroller && anchor) {
          const bounds = scroller.getBoundingClientRect();
          const pageBounds = target.getBoundingClientRect();
          scroller.scrollLeft += pageBounds.left + anchor.x * pageBounds.width - bounds.left - scroller.clientWidth / 2;
          scroller.scrollTop += pageBounds.top + anchor.y * pageBounds.height - bounds.top - scroller.clientHeight / 2;
          zoomAnchorRef.current = null;
        }
      });
    }).then(() => {
      if (!cancelled) setRendering(false);
    }).catch((caught: unknown) => {
      if (cancelled || (caught instanceof Error && caught.name === 'RenderingCancelledException')) return;
      setRendering(false);
      setError(caught instanceof Error ? caught.message : String(caught));
    });

    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [onPageOrientation, pageNumber, pdf, viewportSize.height, viewportSize.width, zoom]);

  const changeZoom = (delta: number) => {
    const scroller = viewportRef.current;
    const canvas = canvasRef.current;
    if (scroller && canvas && canvas.clientWidth && canvas.clientHeight) {
      const bounds = scroller.getBoundingClientRect();
      const page = canvas.getBoundingClientRect();
      zoomAnchorRef.current = {
        x: Math.max(0, Math.min(1, (bounds.left + scroller.clientWidth / 2 - page.left) / page.width)),
        y: Math.max(0, Math.min(1, (bounds.top + scroller.clientHeight / 2 - page.top) / page.height)),
      };
    }
    setZoom((value) => Math.max(0.6, Math.min(2.2, Math.round((value + delta) * 100) / 100)));
  };
  const fitPage = () => {
    zoomAnchorRef.current = null;
    viewportRef.current?.scrollTo(0, 0);
    setZoom(1);
  };
  const pages = pdf?.numPages ?? 0;
  return (
    <div className={`pdf-viewer flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-bg ${className}`} aria-busy={loading || rendering}>
      <div className="flex min-h-10 shrink-0 flex-wrap items-center gap-2 border-b border-line/70 bg-panel px-3 py-1.5 text-[11px] text-ink-dim">
        <span className="min-w-0 flex-1 truncate font-mono text-ink" title={name}>{name}</span>
        <span className="shrink-0 font-mono tabular-nums">
          {zh ? '第' : 'Page'} {pageNumber} / {pages || '…'}
        </span>
        <button
          type="button"
          disabled={!pdf || pageNumber <= 1}
          onClick={() => { viewportRef.current?.scrollTo(0, 0); zoomAnchorRef.current = null; setPageNumber((value) => Math.max(1, value - 1)); }}
          className="rounded border border-line px-2 py-1 hover:border-blue/50 hover:text-ink disabled:opacity-35"
        >
          {zh ? '上一页' : 'Previous'}
        </button>
        <button
          type="button"
          disabled={!pdf || pageNumber >= pages}
          onClick={() => { viewportRef.current?.scrollTo(0, 0); zoomAnchorRef.current = null; setPageNumber((value) => Math.min(pages, value + 1)); }}
          className="rounded border border-line px-2 py-1 hover:border-blue/50 hover:text-ink disabled:opacity-35"
        >
          {zh ? '下一页' : 'Next'}
        </button>
        <button
          type="button"
          aria-label={zh ? '缩小' : 'Zoom out'}
          disabled={!pdf || zoom <= 0.6}
          onClick={() => changeZoom(-0.15)}
          className="flex h-7 w-7 items-center justify-center rounded border border-line hover:border-blue/50 hover:text-ink"
        >
          −
        </button>
        <span className="w-10 text-center font-mono tabular-nums">{Math.round(zoom * 100)}%</span>
        <button
          type="button"
          aria-label={zh ? '放大' : 'Zoom in'}
          disabled={!pdf || zoom >= 2.2}
          onClick={() => changeZoom(0.15)}
          className="flex h-7 w-7 items-center justify-center rounded border border-line hover:border-blue/50 hover:text-ink"
        >
          +
        </button>
        <button type="button" disabled={!pdf} onClick={fitPage} className="rounded border border-line px-2 py-1 hover:border-blue/50 hover:text-ink disabled:opacity-35">
          {zh ? '适合页面' : 'Fit page'}
        </button>
      </div>
      <div ref={viewportRef} className="pdf-scroll-viewport relative min-h-0 min-w-0 flex-1 overflow-auto bg-surface/60 p-4 scroll-thin" tabIndex={0} aria-label={zh ? 'PDF 页面滚动区域' : 'PDF page scroll area'}>
        {loading ? <div className="absolute inset-0 flex items-center justify-center"><Spinner /></div> : null}
        {error ? (
          <div role="alert" className="m-auto max-w-sm rounded border border-err/35 bg-err/5 p-4 text-center text-sm text-err">
            <p>{zh ? 'PDF 暂时无法预览' : 'PDF preview is temporarily unavailable'}</p>
            <button
              type="button"
              onClick={() => {
                resetPdfEngine();
                if (onRetry) onRetry();
                else setLoadAttempt((value) => value + 1);
              }}
              className="mt-3 rounded border border-line bg-panel px-3 py-1.5 text-ink hover:border-blue/50"
            >
              {zh ? '重试预览' : 'Retry preview'}
            </button>
            <details className="mt-3 break-words text-xs text-ink-dim">
              <summary>{zh ? '错误详情' : 'Error details'}</summary>
              {error}
            </details>
          </div>
        ) : null}
        {!error ? (
          <div className="pdf-page-stage flex min-h-full min-w-full w-max items-center justify-center">
            <canvas
              ref={canvasRef}
              role="img"
              aria-label={`${name} · ${zh ? '第' : 'page'} ${pageNumber}`}
              className="block max-w-none shrink-0 bg-white shadow-xl"
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
