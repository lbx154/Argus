import type { EventMsg } from './api.js';
import { renderEvent, messageId, mergeFragment, type Rendered } from './eventRender.js';
import { eventKey } from '../../core/src/events.js';

export interface EventLine {
  ev: EventMsg;
  r: Rendered;
  key: string;
  mid: string;
}

/** Whitelist + coalesce the same way for the live log and searchable panel. */
export function buildEventLines(events: EventMsg[]): EventLine[] {
  const list: EventLine[] = [];
  const idx = new Map<string, number>();
  events.forEach((ev) => {
    const r = renderEvent(ev);
    if (!r) return;
    const mid = messageId(ev);
    if (mid && idx.has(mid)) {
      const index = idx.get(mid)!;
      list[index] = {
        ...list[index],
        r: { ...list[index].r, text: mergeFragment(list[index].r.text, r.text) },
      };
      return;
    }
    const key = mid || eventKey(ev);
    if (mid) idx.set(mid, list.length);
    list.push({ ev, r, key, mid });
  });
  return list;
}
