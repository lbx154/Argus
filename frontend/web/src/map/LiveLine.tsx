import { useEffect, useState } from "react";
import { elapsedLabel } from "./alive";
import { roleActionLabel } from '../lib/taskLanguage';
import { roleLabel } from '../lib/enumLabels';
import { useI18n } from '../i18n';
import type { MapTask } from './model';

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

/** Who holds the task while it is open, and the latest thing done on it. */
export function CardNow({ task, zh }: { task: MapTask; zh: boolean }) {
  const { t } = useI18n();
  const action = task.latest_action;
  const holder = task.status === 'done' ? undefined : task.role;
  if (!holder && !action) return null;
  return (
    <div className="map-card-now" data-testid="map-card-now">
      {holder ? <p className="map-card-said" data-testid="map-card-owner">
        {zh ? '负责人：' : 'Owner: '}{roleLabel(holder, t)}
      </p> : null}
      {action ? <p className={action.kind === 'command' ? 'map-card-running' : 'map-card-said'} data-testid="map-card-action" title={action.text}>
        <b>{roleLabel(action.role, t)}：</b>{action.text}
      </p> : null}
    </div>
  );
}
