import pdfModuleUrl from 'pdfjs-dist/legacy/build/pdf.min.mjs?url';
import pdfWorkerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';

type PdfEngine = typeof import('pdfjs-dist');
let pending: Promise<PdfEngine> | null = null;
let attempt = 0;

export function resetPdfEngine(): void {
  pending = null;
  attempt += 1;
}

/** Keep optional PDF failures inside the preview, including module retries. */
export function loadPdfEngine(): Promise<PdfEngine> {
  if (pending) return pending;
  // The self-contained legacy ESM includes the browser compatibility polyfills.
  // Loading its hashed asset directly also avoids Vite's page-wide recovery
  // event. A new URL on explicit retry bypasses failed browser module records.
  const moduleUrl = new URL(pdfModuleUrl, import.meta.url);
  const workerUrl = new URL(pdfWorkerUrl, import.meta.url);
  if (attempt) {
    moduleUrl.searchParams.set('retry', String(attempt));
    workerUrl.searchParams.set('retry', String(attempt));
  }
  const request: Promise<PdfEngine> = import(/* @vite-ignore */ moduleUrl.href)
    .then((pdfjs: PdfEngine) => {
      pdfjs.GlobalWorkerOptions.workerSrc = workerUrl.href;
      return pdfjs;
    }).catch((error: unknown) => {
      if (pending === request) resetPdfEngine();
      throw error;
    });
  pending = request;
  return request;
}
