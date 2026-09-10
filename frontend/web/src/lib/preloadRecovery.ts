export function installStaleChunkRecovery(
  target: EventTarget,
  reload: () => void,
  options: {
    buildId: string;
    storage: () => Pick<Storage, 'getItem' | 'setItem'>;
  },
): void {
  let reloading = false;
  target.addEventListener('vite:preloadError', (event) => {
    const payload = (event as Event & { payload?: unknown }).payload;
    const message = payload instanceof Error ? payload.message : String(payload ?? '');
    // PDF imports are caught by their preview component. A missing PDF engine
    // or a browser compatibility error must not tear down the entire workbench.
    if (/\/(?:pdf[.-]|pdfjs)[^/\s]*\.(?:m?js)(?:[?#\s]|$)/i.test(message)) return;
    if (!/failed to fetch dynamically imported module|importing a module script failed|loading chunk .+ failed/i.test(message)) return;
    if (reloading) {
      event.preventDefault();
      return;
    }
    // Memory alone resets on navigation and creates an endless reload loop.
    try {
      const storage = options.storage();
      const key = 'argus.stale-chunk-reloaded';
      if (storage.getItem(key) === options.buildId) return;
      storage.setItem(key, options.buildId);
    } catch {
      return;
    }
    event.preventDefault();
    reloading = true;
    reload();
  });
}
