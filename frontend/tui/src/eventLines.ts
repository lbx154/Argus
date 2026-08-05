import type { EventMsg } from './api.js';
import { renderEvent, messageId, mergeFragment, type Rendered } from './eventRender.js';
import { eventKey, fragmentMode } from '../../core/src/events.js';

export interface EventLine {
  ev: EventMsg;
  r: Rendered;
  key: string;
  mid: string;
}

export interface EventLinePartition {
  committed: EventLine[];
  live: EventLine | null;
}

/** Whitelist + coalesce the same way for the live log and searchable panel. */
export function buildEventLines(events: EventMsg[]): EventLine[] {
  const list: EventLine[] = [];
  const idx = new Map<string, number>();
  const recoveredMessageIds = new Set(
    events
      .map((event) => String(
        (event as Record<string, unknown>).recovered_from_message_id ?? '',
      ))
      .filter(Boolean),
  );
  events.forEach((ev) => {
    // Repairs are append-only so the audit log stays intact. In the rendered
    // conversation, hide the truncated row superseded by a recovered delivery.
    if (
      recoveredMessageIds.has(messageId(ev))
      && (ev as Record<string, unknown>).final_delivery !== true
    ) return;
    const r = renderEvent(ev);
    if (!r) return;
    const mid = messageId(ev);
    if (mid && idx.has(mid)) {
      const index = idx.get(mid)!;
      const updated = {
        ...list[index],
        ev: { ...list[index].ev, ...ev },
        r: {
          ...list[index].r,
          ...r,
          text: mergeFragment(list[index].r.text, r.text, fragmentMode(ev)),
        },
      };
      if (index === list.length - 1) {
        list[index] = updated;
        return;
      }
      list.splice(index, 1);
      for (const [knownMid, position] of idx) {
        if (position > index) idx.set(knownMid, position - 1);
      }
      list.push(updated);
      idx.set(mid, list.length - 1);
      return;
    }
    const key = mid || eventKey(ev);
    if (mid) idx.set(mid, list.length);
    list.push({ ev, r, key, mid });
  });
  return list;
}

/**
 * Keep the current streaming row mutable in Ink's live area. Manager supplies
 * an explicit live id; role progress uses replace=true and settles as soon as a
 * later milestone/event arrives.
 */
export function partitionEventLines(
  lines: EventLine[],
  liveMessageId = '',
): EventLinePartition {
  const last = lines.at(-1);
  const explicitLiveIndex = liveMessageId
    ? lines.findIndex((line) => line.mid === liveMessageId)
    : -1;
  const replaceableRoleProgress = Boolean(
    last?.mid
    && last.ev.type === 'engineer.progress'
    && last.ev.replace === true,
  );
  const liveIndex = explicitLiveIndex >= 0
    ? explicitLiveIndex
    : replaceableRoleProgress
    ? lines.length - 1
    : -1;
  const live = liveIndex >= 0 ? lines[liveIndex] : null;
  return {
    committed: live
      ? lines.filter((_, index) => index !== liveIndex)
      : lines,
    live,
  };
}
