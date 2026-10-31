import { useEffect, useState } from "react";
import { elapsedLabel } from "./alive";
import { roleActionLabel } from '../lib/taskLanguage';
import { roleLabel } from '../lib/enumLabels';
import { useI18n } from '../i18n';
import { MarkdownExcerpt } from '../components/MarkdownExcerpt';
import type { MapTask } from './model';

/** How long since `since` (epoch seconds), ticking each second; null with no start. */
export function useElapsed(since?: number | null): number | null {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (since == null) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [since]);
  return since == null ? null : now - since;
}

/** One quiet sentence on the card being worked on right now: who is at it and
 * for how long this call. The clock ticks on its own so the rest of the card
 * stays still. */
export function LiveLine({ role, since, zh, replying = false }: { role?: string; since?: number | null; zh: boolean; replying?: boolean }) {
  const seconds = useElapsed(since);
  const elapsed = seconds != null ? elapsedLabel(seconds, zh) : null;
  return (
    <div className="map-card-live" data-testid="map-card-live" aria-live="off">
      <i aria-hidden="true" />
      <span>
        {roleActionLabel(role, zh, '', '', replying)}
        {elapsed ? (zh ? ` · 本次已进行 ${elapsed}` : ` · ${elapsed} this run`) : ""}
      </span>
    </div>
  );
}

/** The latest thing said on the task, and who holds it while it is open when
 * that is someone else. */
export function CardNow({ task, zh }: { task: MapTask; zh: boolean }) {
  const { t } = useI18n();
  if (task.status === 'done' && task.turn_kind !== 'qa') return null;
  const said = task.latest_action;
  const holder = task.status === 'done' || task.role === said?.role ? undefined : task.role;
  if (!holder && !said) return null;
  return (
    <div className="map-card-now" data-testid="map-card-now">
      {holder ? <p className="map-card-said" data-testid="map-card-owner">
        {zh ? '负责人：' : 'Owner: '}{roleLabel(holder, t)}
      </p> : null}
      {said ? <p className="map-card-said" data-testid="map-card-action" title={said.text}>
        <b>{roleLabel(said.role, t)}：</b><MarkdownExcerpt>{said.text}</MarkdownExcerpt>
      </p> : null}
    </div>
  );
}
