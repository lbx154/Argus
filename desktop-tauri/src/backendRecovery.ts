import type { DesktopStatus } from './backendStatus';

/** One notification per recovered backend, not per health/status repaint.
 * Keep readiness pending while the cockpit is hidden or still loading. */
export function backendRecoveryNotifications(
  send: (generation: number, origin: string) => void,
  cockpitOrigin: () => string | null,
) {
  let previous: DesktopStatus | undefined;
  let generation = 0;
  let delivered = 0;
  let listening = false;
  const flush = () => {
    if (!listening || previous?.state !== 'ready' || generation === delivered) return;
    const origin = cockpitOrigin();
    if (!origin || !previous.url) return;
    try { if (new URL(previous.url).origin !== origin) return; } catch { return; }
    delivered = generation;
    send(generation, origin);
  };
  return {
    observe(status: DesktopStatus) {
      if (status.state === 'ready' && (previous?.state !== 'ready'
        || status.pid !== previous.pid || status.url !== previous.url
        || (previous.warning && !status.warning))) generation++;
      previous = status;
      flush();
    },
    listening() { listening = true; flush(); },
    navigating() { listening = false; delivered = 0; },
    flush,
  };
}
