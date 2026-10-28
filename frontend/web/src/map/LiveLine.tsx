import { useContext, useEffect, useState } from "react";
import { elapsedLabel } from "./alive";
import { roleActionLabel } from '../lib/taskLanguage';
import { roleLabel } from '../lib/enumLabels';
import { useI18n } from '../i18n';
import { MapCardNowContext } from './cardContext';

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

/** What the running task is doing, under where it stands: the newest thing a
 * role said about it and the command still running, as the conversation's
 * live line says them. Reads the context MapPanel fills on each poll, so only
 * these lines re-render. */
export function CardNow({ taskId, zh }: { taskId: string; zh: boolean }) {
  const { t } = useI18n();
  const live = useContext(MapCardNowContext);
  if (!live || live.taskId !== taskId || (!live.said && !live.running)) return null;
  return (
    <div className="map-card-now" data-testid="map-card-now">
      {live.said ? <p className="map-card-said" data-testid="map-card-said" title={live.said.text}>
        <b>{roleLabel(live.said.role, t)}：</b>{live.said.text}
      </p> : null}
      {live.running ? <p className="map-card-running" data-testid="map-card-running" title={live.running.text}>
        {zh ? '正在' : 'Now: '}{live.running.text}
      </p> : null}
    </div>
  );
}
