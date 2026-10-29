import { useCallback, useEffect, useRef, useState } from 'react';
import type { DeliveryReceipt } from '../../core/src/types';
import { readLocalStorage, writeLocalStorage } from './lib/storage';
import { deliveryFiles } from './components/deliveryPresentation';

function readSeen(key: string): string[] {
  try { const ids: unknown = JSON.parse(readLocalStorage(key) || '[]'); return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : []; } catch { return []; }
}

/** How long a new result's announcement stays: the receipt in the conversation and the
 * results count stay after it. */
export const FRESH_FOR_MS = 8_000;

/** Historical receipts stay accessible; a newly received file delivery is announced
 * (badge + a toast that goes by itself) but never opens the full-screen viewer on its own,
 * so the reply and the composer draft stay visible. */
export function useDeliveryCenter(sid: string | null, ready: boolean, receipt: DeliveryReceipt | null, canAutoOpen = true) {
  const observed = useRef<{ sid: string; ids: Set<string> }>();
  const [selection, setSelection] = useState<{ sid: string; receipt: DeliveryReceipt; path: string | null } | null>(null);
  const [fresh, setFresh] = useState<{ sid: string; receipt: DeliveryReceipt } | null>(null);
  const markSeen = useCallback((id: string) => {
    if (!sid) return;
    const key = `argus.delivery.seen.v1:${sid}`;
    writeLocalStorage(key, JSON.stringify([...new Set([...readSeen(key), id])].slice(-80)));
  }, [sid]);
  const open = useCallback((next: DeliveryReceipt, preferredPath?: string | null) => {
    if (!sid) return;
    markSeen(next.delivery_id);
    const files = deliveryFiles(next);
    const path = files.find((file) => file.path === preferredPath)?.path || files[0]?.path || null;
    setSelection({ sid, receipt: next, path });
    setFresh(null);
  }, [sid, markSeen]);
  useEffect(() => {
    if (!sid || !ready) return;
    const id = receipt?.delivery_id;
    if (observed.current?.sid !== sid) {
      observed.current = { sid, ids: new Set(id ? [id] : []) };
      setSelection(null);
      setFresh(null);
      return;
    }
    if (!id || observed.current.ids.has(id) || !canAutoOpen) return;
    observed.current.ids.add(id);
    if (receipt && deliveryFiles(receipt).length && !readSeen(`argus.delivery.seen.v1:${sid}`).includes(id)) setFresh({ sid, receipt });
  }, [sid, ready, receipt, canAutoOpen]);
  useEffect(() => {
    if (!fresh) return;
    const timer = setTimeout(() => setFresh(null), FRESH_FOR_MS);
    return () => clearTimeout(timer);
  }, [fresh]);
  return {
    selection: selection?.sid === sid ? selection : null,
    open,
    fresh: fresh?.sid === sid ? fresh.receipt : null,
    dismissFresh: useCallback(() => setFresh(null), []),
    close: useCallback(() => setSelection(null), []),
    selectPath: useCallback((path: string) => setSelection((current) => current ? { ...current, path } : current), []),
  };
}
