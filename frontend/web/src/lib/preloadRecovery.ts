const RELOAD_KEY = 'argus.chunk-reload.v1';
const RELOAD_WINDOW_MS = 60_000;
type ReloadStorage = Pick<Storage, 'getItem' | 'setItem'>;

function browserStorage(): ReloadStorage | undefined {
  try { return globalThis.sessionStorage; } catch { return undefined; }
}

export function installStaleChunkRecovery(
  target: EventTarget,
  reload: () => void,
  storage: ReloadStorage | undefined = browserStorage(),
): void {
  let reloading = false;
  target.addEventListener('vite:preloadError', (event) => {
    const payload = (event as Event & { payload?: unknown }).payload;
    const message = payload instanceof Error ? payload.message : String(payload ?? '');
    // Evaluation errors are code defects, not stale downloads. Let the import
    // reject so the workspace error boundary can explain them instead of
    // swallowing the error and handing React.lazy an undefined module.
    if (!/Failed to fetch dynamically imported module|error loading dynamically imported module|Loading chunk .* failed|Unable to preload CSS/i.test(message)) return;
    if (reloading) { event.preventDefault(); return; }
    // Persist the attempt across navigation. An offline tab or a broken bundle
    // must not reload forever; without storage, leave recovery to the user.
    if (!storage) return;
    try {
      const last = Number(storage.getItem(RELOAD_KEY));
      const now = Date.now();
      if (last > 0 && now - last < RELOAD_WINDOW_MS) return;
      storage.setItem(RELOAD_KEY, String(now));
    } catch { return; }
    event.preventDefault();
    reloading = true;
    reload();
  });
}
