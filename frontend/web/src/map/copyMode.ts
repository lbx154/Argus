export type ReaderPreview = 'source-first' | 'learning-path' | null;

/** Keep HTTP selection and both readers' browser caches in the same mode. */
export function readerPreview(): ReaderPreview {
  const page = new URLSearchParams(typeof window === 'undefined' ? '' : window.location.search);
  const preview = page.get('reader_preview');
  return preview === 'source-first' || preview === 'learning-path' ? preview : null;
}

export function mapCopyKey(source: string, name: string, locale: string, sessionId?: string, preview = readerPreview()) {
  const key = ['map-copy', source, name, locale, sessionId];
  return preview ? [...key, preview] : key;
}
