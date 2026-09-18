import { useCallback, useEffect, useReducer, useRef, type MutableRefObject } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import type { DeliveryReceipt } from '../../core/src/types';
import { api } from './api';
import type { NoticeTone } from './components/ActionNotice';
import { deliveryFiles } from './components/deliveryPresentation';
import { deliveryNotificationPayload, isDesktopSessionId, subscribeDesktopDelivery, type DesktopDeliveryNotification } from './lib/desktopBridge';
import { errorText } from './lib/format';

interface Options {
  selectedSid: string | null;
  loadedSid: string | null;
  sidRef: MutableRefObject<string | null>;
  snapshotError: unknown;
  queryClient: QueryClient;
  receipts: DeliveryReceipt[];
  completionId: string;
  selectProject: (sid: string) => void;
  openDelivery: (receipt: DeliveryReceipt, path?: string | null) => void;
  focusPath: (path: string) => void;
  noPath: () => void;
  notify: (tone: NoticeTone, text: string) => void;
}
interface Intent {
  payload: DesktopDeliveryNotification;
  target: string | null;
  phase: 'resolving' | 'loading' | 'opening';
  controller: AbortController;
}

/** Only evidence already known in the currently loaded session can resolve an
 * old toast. Do not parse a sid out of deliveryId or scan other projects. */
function legacyTarget(payload: DesktopDeliveryNotification, current: Options): string | null {
  if (!current.loadedSid || current.loadedSid !== current.sidRef.current) return null;
  const matches = current.receipts.filter(receipt => {
    const known = deliveryNotificationPayload(receipt);
    return known?.deliveryId === payload.deliveryId
      && known.title === payload.title && known.summary === payload.summary
      && (!payload.path || deliveryFiles(receipt).some(file => file.path === payload.path));
  });
  const completion = Boolean(current.completionId && current.completionId === payload.deliveryId);
  return matches.length + Number(completion) === 1 ? current.loadedSid : null;
}

/** Native activation restores the window; this hook owns only session/file
 * navigation. A manual selection (even of the same sid) invalidates the intent. */
export function useDesktopDeliveryNavigation(options: Options): () => void {
  const latest = useRef(options);
  latest.current = options;
  const pending = useRef<Intent | null>(null);
  const [turn, advance] = useReducer((value: number) => value + 1, 0);
  const cancel = useCallback(() => {
    pending.current?.controller.abort();
    pending.current = null;
  }, []);
  const fail = useCallback((intent: Intent, error: unknown) => {
    if (pending.current !== intent) return;
    cancel();
    latest.current.notify('error', `Cannot open notification: ${errorText(error)}. No other session was used.`);
  }, [cancel]);

  useEffect(() => {
    const unsubscribe = subscribeDesktopDelivery(payload => {
      cancel();
      const target = payload.sessionId ?? legacyTarget(payload, latest.current);
      if (!target) {
        latest.current.notify('info', 'This older notification cannot be automatically located. Select its session and open the result manually.');
        return;
      }
      if (!isDesktopSessionId(target)) {
        latest.current.notify('error', 'Cannot open notification: invalid session identity.');
        return;
      }
      const intent: Intent = { payload, target: null, phase: 'resolving', controller: new AbortController() };
      pending.current = intent;
      // Use the existing authenticated index/query cache and selection path.
      // Index reads are shared with normal UI refreshes, so cancelling a toast
      // does not abort the shared query or another session's background work.
      void latest.current.queryClient.fetchQuery({ queryKey: ['projects'], queryFn: api.projectIndex, staleTime: 0 }).then(index => {
        if (pending.current !== intent) return;
        if (!index.projects.some(project => project.id === target)) throw new Error('the target session is unavailable or no longer accessible');
        intent.target = target;
        intent.phase = 'loading';
        latest.current.selectProject(target);
        advance();
      }).catch(error => fail(intent, error));
    });
    window.addEventListener('popstate', cancel);
    return () => { unsubscribe(); window.removeEventListener('popstate', cancel); cancel(); };
  }, [cancel, fail]);

  useEffect(() => {
    const intent = pending.current;
    if (!intent?.target) return;
    const current = latest.current;
    if (current.sidRef.current !== intent.target) { cancel(); return; }
    if (intent.phase !== 'loading') return;
    if (current.snapshotError) { fail(intent, current.snapshotError); return; }
    if (current.loadedSid !== intent.target) return;
    intent.phase = 'opening';
    const path = intent.payload.path;
    if (!path) {
      pending.current = null;
      current.noPath();
      current.notify('info', 'Notification session opened. This notification does not identify a file.');
      return;
    }
    // Check the exact target file before using the existing preview. In
    // particular, never let its default-file fallback guess a path in B.
    void api.artifact(intent.target, path, intent.controller.signal).then(artifact => {
      const now = latest.current;
      if (pending.current !== intent || now.sidRef.current !== intent.target || now.loadedSid !== intent.target) return;
      if (!artifact.exists || artifact.path !== path) throw new Error('the target file is unavailable');
      pending.current = null;
      const receipt = now.receipts.find(row => row.delivery_id === intent.payload.deliveryId
        && deliveryFiles(row).some(file => file.path === path));
      if (receipt) now.openDelivery(receipt, path);
      else now.focusPath(path);
    }).catch(error => fail(intent, error));
  }, [options.selectedSid, options.loadedSid, options.snapshotError, turn, cancel, fail]);

  return cancel;
}
