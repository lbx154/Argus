import { useCallback, useEffect, useState } from 'react';
import type { DeliveryReceipt } from '../../core/src/types';
import { deliveryFiles } from './components/deliveryPresentation';

/** A result opens only when the reader chooses it. */
export function useDeliveryCenter(sid: string | null) {
  const [selection, setSelection] = useState<{ sid: string; receipt: DeliveryReceipt; path: string | null } | null>(null);
  useEffect(() => setSelection(null), [sid]);
  const open = useCallback((receipt: DeliveryReceipt, preferredPath?: string | null) => {
    if (!sid) return;
    const files = deliveryFiles(receipt);
    const path = files.find(file => file.path === preferredPath)?.path || files[0]?.path || null;
    setSelection({ sid, receipt, path });
  }, [sid]);
  return {
    selection: selection?.sid === sid ? selection : null,
    open,
    close: useCallback(() => setSelection(null), []),
    selectPath: useCallback((path: string) => setSelection(current => current ? { ...current, path } : current), []),
  };
}
