import { useEffect, useRef, useState } from 'react';
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  RenderTask,
} from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { useI18n } from '../i18n';
import { Spinner } from './primitives';

export function pdfContainScale(
  pageWidth: number,
  pageHeight: number,
  viewportWidth: number,
  viewportHeight: number,
): number {
  const widthScale = Math.max(1, viewportWidth - 32) / Math.max(1, pageWidth);
  const heightScale = Math.max(1, viewportHeight - 32) / Math.max(1, pageHeight);
  return Math.max(0.25, Math.min(2.5, widthScale, heightScale));
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
}: {
  src: string;
  name: string;
  className?: string;
  onPageOrientation?: (orientation: 'portrait' | 'landscape') => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 });
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState('');

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
    setError('');
    setLoading(true);

    void Promise.all([
      fetch(src, { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error(`PDF request failed (${response.status})`);
        return response.arrayBuffer();
      }),
      import('pdfjs-dist'),
    ]).then(async ([bytes, pdfjs]) => {
      if (!alive) return;
      pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
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
      void task?.destroy();
    };
  }, [src]);

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
      const target = canvasRef.current;
      const context = target.getContext('2d', { alpha: false });
      if (!context) throw new Error('Canvas rendering is unavailable');
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      target.width = Math.max(1, Math.floor(viewport.width * pixelRatio));
      target.height = Math.max(1, Math.floor(viewport.height * pixelRatio));
      target.style.width = `${viewport.width}px`;
      target.style.height = `${viewport.height}px`;
      renderTask = page.render({
        canvas: target,
        canvasContext: context,
        viewport,
        transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
      });
      return renderTask.promise;
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

  const pages = pdf?.numPages ?? 0;
  return (
    <div className={`flex min-h-0 flex-1 flex-col bg-bg ${className}`}>
      <div className="flex min-h-10 shrink-0 flex-wrap items-center gap-2 border-b border-line/70 bg-panel px-3 py-1.5 text-[11px] text-ink-dim">
        <span className="min-w-0 flex-1 truncate font-mono text-ink" title={name}>{name}</span>
        <span className="shrink-0 font-mono tabular-nums">
          {zh ? '第' : 'Page'} {pageNumber} / {pages || '…'}
        </span>
        <button
          type="button"
          disabled={!pdf || pageNumber <= 1}
          onClick={() => setPageNumber((value) => Math.max(1, value - 1))}
          className="rounded border border-line px-2 py-1 hover:border-blue/50 hover:text-ink disabled:opacity-35"
        >
          {zh ? '上一页' : 'Previous'}
        </button>
        <button
          type="button"
          disabled={!pdf || pageNumber >= pages}
          onClick={() => setPageNumber((value) => Math.min(pages, value + 1))}
          className="rounded border border-line px-2 py-1 hover:border-blue/50 hover:text-ink disabled:opacity-35"
        >
          {zh ? '下一页' : 'Next'}
        </button>
        <button
          type="button"
          aria-label={zh ? '缩小' : 'Zoom out'}
          onClick={() => setZoom((value) => Math.max(0.6, value - 0.15))}
          className="flex h-7 w-7 items-center justify-center rounded border border-line hover:border-blue/50 hover:text-ink"
        >
          −
        </button>
        <span className="w-10 text-center font-mono tabular-nums">{Math.round(zoom * 100)}%</span>
        <button
          type="button"
          aria-label={zh ? '放大' : 'Zoom in'}
          onClick={() => setZoom((value) => Math.min(2.2, value + 0.15))}
          className="flex h-7 w-7 items-center justify-center rounded border border-line hover:border-blue/50 hover:text-ink"
        >
          +
        </button>
      </div>
      <div ref={viewportRef} className="relative min-h-0 flex-1 overflow-auto bg-surface/60 p-4 scroll-thin">
        {loading ? <div className="absolute inset-0 flex items-center justify-center"><Spinner /></div> : null}
        {error ? (
          <div className="m-auto max-w-sm rounded border border-err/35 bg-err/5 p-4 text-center text-sm text-err">
            {zh ? 'PDF 无法渲染' : 'Unable to render PDF'} · {error}
          </div>
        ) : null}
        {!error ? (
          <div className="flex min-h-full min-w-full items-center justify-center">
            <canvas
              ref={canvasRef}
              role="img"
              aria-label={`${name} · ${zh ? '第' : 'page'} ${pageNumber}`}
              className={`bg-white shadow-xl transition-opacity ${rendering || loading ? 'opacity-45' : 'opacity-100'}`}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
