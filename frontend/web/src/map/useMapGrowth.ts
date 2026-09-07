import { useEffect, useRef, useState } from 'react';
import { emptyGrowth, GrowthLedger, type GrowthScene } from './growth';

export function useMapGrowth(scene: GrowthScene, historyLoading = false) {
  const ledger = useRef(new GrowthLedger());
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const [batch, setBatch] = useState(emptyGrowth);
  useEffect(() => {
    const next = ledger.current.observe(scene, historyLoading);
    if (historyLoading) {
      timers.current.forEach(clearTimeout);
      timers.current.clear();
      setBatch(emptyGrowth());
    } else if (next) {
      // A second update must not cut short an earlier entrance. Each batch
      // retires separately, so off-screen cards cannot replay indefinitely.
      setBatch((current) => ({ revision: next.revision,
        cards: { ...current.cards, ...next.cards },
        steps: { ...current.steps, ...next.steps },
        links: { ...current.links, ...next.links },
      }));
      const timer = setTimeout(() => {
        timers.current.delete(timer);
        setBatch((current) => ({ revision: current.revision,
          cards: Object.fromEntries(Object.entries(current.cards).filter(([id]) => !(id in next.cards))),
          steps: Object.fromEntries(Object.entries(current.steps).filter(([id]) => !(id in next.steps))),
          links: Object.fromEntries(Object.entries(current.links).filter(([id]) => !(id in next.links))),
        }));
      }, 2000);
      timers.current.add(timer);
    }
  }, [scene, historyLoading]);
  useEffect(() => () => {
    timers.current.forEach(clearTimeout);
    timers.current.clear();
  }, []);
  return batch;
}
