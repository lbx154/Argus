import { useEffect, useState } from "react";
import { elapsedLabel, roleName } from "./alive";

/** One quiet sentence on the card being worked on right now: who is at it and
 * for how long. The clock ticks on its own so the rest of the card stays still. */
export function LiveLine({ role, since, zh }: { role?: string; since?: number | null; zh: boolean }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (since == null) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [since]);
  const who = roleName(role, zh);
  const elapsed = since != null ? elapsedLabel(now - since, zh) : null;
  return (
    <div className="map-card-live" data-testid="map-card-live" aria-live="off">
      <i aria-hidden="true" />
      <span>
        {zh ? `${who}正在处理` : `${who} at work`}
        {elapsed ? (zh ? ` · 已进行 ${elapsed}` : ` · ${elapsed}`) : ""}
      </span>
    </div>
  );
}
