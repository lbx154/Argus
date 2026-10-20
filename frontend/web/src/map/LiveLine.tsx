import { useEffect, useState } from "react";
import { elapsedLabel } from "./alive";
import { roleActionLabel } from '../lib/taskLanguage';

/** One quiet sentence on the card being worked on right now: who is at it and
 * for how long this run. The clock ticks on its own so the rest of the card
 * stays still. */
export function LiveLine({ role, since, zh }: { role?: string; since?: number | null; zh: boolean }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (since == null) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [since]);
  const elapsed = since != null ? elapsedLabel(now - since, zh) : null;
  return (
    <div className="map-card-live" data-testid="map-card-live" aria-live="off">
      <i aria-hidden="true" />
      <span>
        {roleActionLabel(role, zh)}
        {elapsed ? (zh ? ` · 本次已进行 ${elapsed}` : ` · ${elapsed} this run`) : ""}
      </span>
    </div>
  );
}
