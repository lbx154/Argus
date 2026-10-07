import { useEffect, useState } from "react";
import { elapsedLabel, roleName } from "./alive";

/** One quiet sentence on the card being worked on right now: who is at it and
 * for how long this run. The clock ticks on its own so the rest of the card
 * stays still. Given `text`, the line says that instead: the same sentence
 * the sidebar and the conversation header use for a task waiting on its team. */
export function LiveLine({ role, since, zh, text }: { role?: string; since?: number | null; zh: boolean; text?: string }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (since == null || text) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [since, text]);
  const who = roleName(role, zh);
  const elapsed = since != null ? elapsedLabel(now - since, zh) : null;
  return (
    <div className="map-card-live" data-testid="map-card-live" data-waiting={!!text || undefined} aria-live="off">
      <i aria-hidden="true" />
      <span title={text}>
        {text ? text : <>
          {zh ? `${who}正在处理` : `${who} at work`}
          {elapsed ? (zh ? ` · 本次已进行 ${elapsed}` : ` · ${elapsed} this run`) : ""}
        </>}
      </span>
    </div>
  );
}
