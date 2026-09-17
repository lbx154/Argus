import { RELEASE_ID } from '../../../core/src/release.generated';

// Responses already carry this identity. Observe normal polling instead of
// adding another timer; the one-time API handshake cannot detect deployments.
let pageUpdateAvailable = false;
const listeners = new Set<() => void>();

export const getPageUpdateAvailable = () => pageUpdateAvailable;
export const subscribePageUpdate = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

export function observePageRelease(release: string | null | undefined): void {
  // Once seen, keep the notice until navigation. An older in-flight response
  // can arrive last; it must not re-enable stale decisions or hide the notice.
  if (pageUpdateAvailable || !release || release === 'unknown' || release === RELEASE_ID) return;
  pageUpdateAvailable = true;
  listeners.forEach(listener => listener());
}

export class PageUpdateRequiredError extends Error {
  constructor() {
    super('页面已更新，请刷新后继续。 / This page has been updated. Refresh to continue.');
    this.name = 'PageUpdateRequiredError';
  }
}

export function requireCurrentPage(): void {
  if (pageUpdateAvailable) throw new PageUpdateRequiredError();
}
