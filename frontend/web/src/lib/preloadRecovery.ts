export function installStaleChunkRecovery(
  target: EventTarget,
  reload: () => void,
): void {
  let reloading = false;
  target.addEventListener('vite:preloadError', (event) => {
    event.preventDefault();
    if (reloading) return;
    reloading = true;
    reload();
  });
}
