import { useCallback, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../../core/src/http";
import { api } from "../api";

/** What a person placed on the map: their cards, their links, and every card
 * they moved. Argus's own cards are not stored here; a task card with a
 * position here keeps it and is never laid out again (experience rule 5). */
export interface CanvasRecord {
  version: number;
  revision: number;
  cards: HumanCard[];
  links: HumanLink[];
  positions: Record<string, Point>;
}
export interface Point { x: number; y: number }
export interface Box extends Point { width: number; height: number }
export interface CanvasAttachment {
  attachment_id: string;
  name: string;
  relative_path: string;
  mime: string;
  size_bytes: number;
}
export interface HumanCard {
  id: string;
  kind: "idea" | "file";
  text: string;
  attachment: CanvasAttachment | null;
  created_ts: number;
  updated_ts: number;
}
export type LinkKind = "basis" | "order" | "check" | "related";
export interface HumanLink { id: string; source: string; target: string; kind: LinkKind; created_ts: number }

export const LINK_KINDS: ReadonlyArray<[LinkKind, string, string]> = [
  ["basis", "依据", "Evidence for"],
  ["order", "先后", "Comes before"],
  ["check", "检查", "Checks"],
  ["related", "相关", "Related"],
];
export const linkKindLabel = (kind: LinkKind, zh: boolean) =>
  LINK_KINDS.find(([k]) => k === kind)?.[zh ? 1 : 2] ?? kind;

export const EMPTY_CANVAS: CanvasRecord = { version: 1, revision: 0, cards: [], links: [], positions: {} };
/** Flow-space size of a person's card; task cards are ~1440 wide, so these
 * read at the same zoom without covering a task. */
export const HUMAN_FRAME = { idea: { width: 860, height: 480 }, file: { width: 860, height: 320 } } as const;
export const humanFrame = (card: Pick<HumanCard, "kind">) => HUMAN_FRAME[card.kind];
export const isHumanCardId = (id: string) => id.startsWith("hc-");

const GAP = 120;
const overlaps = (a: Box, b: Box, gap = GAP) =>
  a.x < b.x + b.width + gap && b.x < a.x + a.width + gap &&
  a.y < b.y + b.height + gap && b.y < a.y + a.height + gap;

/**
 * Argus's cards keep their laid-out place unless that place covers a card a
 * person put down or moved. Such a card tries beside the obstacle (below,
 * right, above, left) and otherwise goes under everything; it never moves a
 * person's card. Pinned task cards take their remembered place.
 */
export function placeAroundPeople(
  laidOut: Record<string, Point>,
  frames: Record<string, { width: number; height: number }>,
  pinned: Record<string, Point>,
  people: Box[],
): Record<string, Point> {
  const ids = Object.keys(laidOut);
  const obstacles: Box[] = [...people];
  for (const id of ids) if (pinned[id] && frames[id]) obstacles.push({ ...pinned[id], ...frames[id] });
  if (!obstacles.length) return laidOut;
  const out: Record<string, Point> = {};
  const taken: Box[] = [];
  const free = (box: Box, self: string) => !obstacles.some((o) => overlaps(box, o)) &&
    !taken.some((t) => overlaps(box, t, 0)) &&
    !ids.some((other) => other !== self && !pinned[other] && !(other in out) && frames[other] &&
      overlaps(box, { ...laidOut[other], ...frames[other] }, 0));
  let changed = false;
  for (const id of ids) {
    const frame = frames[id];
    if (pinned[id] || !frame) { out[id] = pinned[id] ?? laidOut[id]; changed ||= !!pinned[id]; continue; }
    const home = { ...laidOut[id], ...frame };
    const hit = obstacles.find((o) => overlaps(home, o));
    let place: Point = laidOut[id];
    if (hit) {
      const candidates: Point[] = [
        { x: home.x, y: hit.y + hit.height + GAP },
        { x: hit.x + hit.width + GAP, y: home.y },
        { x: home.x, y: hit.y - frame.height - GAP },
        { x: hit.x - frame.width - GAP, y: home.y },
      ];
      place = candidates.find((p) => free({ ...p, ...frame }, id)) ?? {
        x: home.x,
        y: Math.max(...[...obstacles, ...taken].map((b) => b.y + b.height)) + GAP,
      };
      changed = true;
    }
    out[id] = place;
    taken.push({ ...place, ...frame });
  }
  return changed ? out : laidOut;
}

const KEY = (sid: string) => ["map-canvas", sid] as const;

/** The project's canvas with serialized, revision-checked writes. A write
 * based on a stale revision refreshes the canvas once and is tried again, so
 * two windows add to each other's canvas instead of overwriting it. */
export function useCanvas(sessionId: string, enabled: boolean) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: KEY(sessionId),
    queryFn: ({ signal }) => api.mapCanvas(sessionId, signal),
    enabled,
    staleTime: 30_000,
  });
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const write = useCallback(<T extends { canvas: CanvasRecord }>(
    method: "POST" | "PATCH" | "PUT" | "DELETE",
    path: (revision: number) => string,
    body?: (revision: number) => Record<string, unknown>,
  ): Promise<T> => {
    const run = async (): Promise<T> => {
      for (let attempt = 0; ; attempt++) {
        const revision = client.getQueryData<CanvasRecord>(KEY(sessionId))?.revision ?? 0;
        try {
          const result = await api.writeMapCanvas<T>(sessionId, method, path(revision), body?.(revision));
          client.setQueryData(KEY(sessionId), result.canvas);
          return result;
        } catch (error) {
          if (!(error instanceof ApiError && error.status === 409) || attempt > 0) throw error;
          client.setQueryData(KEY(sessionId), await api.mapCanvas(sessionId));
        }
      }
    };
    const next = chain.current.then(run, run);
    chain.current = next.catch(() => undefined);
    return next;
  }, [client, sessionId]);
  const canvas = query.data ?? EMPTY_CANVAS;
  const addCard = useCallback((card: { kind: HumanCard["kind"]; text?: string; attachment?: CanvasAttachment; position: Point }) =>
    write<{ canvas: CanvasRecord; card: HumanCard }>("POST", () => "/cards", (revision) => ({ revision, ...card })), [write]);
  const editCard = useCallback((id: string, text: string) =>
    write<{ canvas: CanvasRecord; card: HumanCard }>("PATCH", () => `/cards/${encodeURIComponent(id)}`, (revision) => ({ revision, text })), [write]);
  const deleteCard = useCallback((id: string) =>
    write("DELETE", (revision) => `/cards/${encodeURIComponent(id)}?revision=${revision}`), [write]);
  const addLink = useCallback((source: string, target: string, kind: LinkKind) =>
    write<{ canvas: CanvasRecord; link: HumanLink }>("POST", () => "/links", (revision) => ({ revision, source, target, kind })), [write]);
  const setLinkKind = useCallback((id: string, kind: LinkKind) =>
    write("PATCH", () => `/links/${encodeURIComponent(id)}`, (revision) => ({ revision, kind })), [write]);
  const deleteLink = useCallback((id: string) =>
    write("DELETE", (revision) => `/links/${encodeURIComponent(id)}?revision=${revision}`), [write]);
  const move = useCallback((positions: Record<string, Point>) => {
    // The card stays where it was dropped while the write is in flight.
    client.setQueryData<CanvasRecord>(KEY(sessionId), (current) => current &&
      { ...current, positions: { ...current.positions, ...positions } });
    return write("PUT", () => "/positions", (revision) => ({ revision, positions })).catch((error) => {
      void client.invalidateQueries({ queryKey: KEY(sessionId) });
      throw error;
    });
  }, [client, sessionId, write]);
  return { canvas, ready: query.isSuccess, addCard, editCard, deleteCard, addLink, setLinkKind, deleteLink, move };
}
export type CanvasApi = ReturnType<typeof useCanvas>;
