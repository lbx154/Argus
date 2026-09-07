import { useCallback, useEffect, useRef, useState } from 'react';
import type { DeliveryReceipt } from '../../core/src/types';
import { readLocalStorage, writeLocalStorage } from './lib/storage';
import { deliveryFiles } from './components/deliveryPresentation';

function readSeen(key: string): string[] {
  try { const ids: unknown = JSON.parse(readLocalStorage(key) || '[]'); return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : []; } catch { return []; }
}

/** Historical receipts stay accessible; only a newly received file delivery interrupts. */
export function useDeliveryCenter(sid: string | null, ready: boolean, receipt: DeliveryReceipt | null, canAutoOpen = true) {
  const observed = useRef<{ sid: string; ids: Set<string> }>();
  const [selection, setSelection] = useState<{ sid: string; receipt: DeliveryReceipt; path: string | null } | null>(null);
  const markSeen = useCallback((id: string) => {
    if (!sid) return;
    const key = `argus.delivery.seen.v1:${sid}`;
    writeLocalStorage(key, JSON.stringify([...new Set([...readSeen(key), id])].slice(-80)));
  }, [sid]);
  const open = useCallback((next: DeliveryReceipt) => {
    if (!sid) return;
    markSeen(next.delivery_id);
    setSelection({ sid, receipt: next, path: deliveryFiles(next)[0]?.path || null });
  }, [sid, markSeen]);
  useEffect(() => {
    if (!sid || !ready) return;
    const id = receipt?.delivery_id;
    if (observed.current?.sid !== sid) {
      observed.current = { sid, ids: new Set(id ? [id] : []) };
      setSelection(null);
      return;
    }
    if (!id || observed.current.ids.has(id) || !canAutoOpen) return;
    observed.current.ids.add(id);
    if (receipt && deliveryFiles(receipt).length && !readSeen(`argus.delivery.seen.v1:${sid}`).includes(id)) open(receipt);
  }, [sid, ready, receipt, open, canAutoOpen]);
  return {
    selection: selection?.sid === sid ? selection : null,
    open,
    close: useCallback(() => setSelection(null), []),
    selectPath: useCallback((path: string) => setSelection((current) => current ? { ...current, path } : current), []),
  };
}
