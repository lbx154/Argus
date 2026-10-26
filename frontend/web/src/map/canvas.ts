import { useCallback, useRef } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
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
export const HUMAN_FRAME = { idea: { width: 880, height: 480 }, file: { width: 880, height: 360 } } as const;
/** Type metrics of a person's card body (canvas.css), in flow units. */
export const CARD_TEXT = { font: 60, line: 87, width: 880 - 2 * 64, chrome: { idea: 210, file: 330 }, maxLines: { idea: 12, file: 6 } } as const;

/** Lines the words take at the card's body size: a CJK character is about
 * one em wide, Latin about half; each paragraph starts a line. */
export function textLines(text: string): number {
  // One em short of the measure, so a line that wraps early still fits.
  const perLine = Math.floor(CARD_TEXT.width / CARD_TEXT.font) - 1;
  return text.split("\n").reduce((sum, paragraph) => {
    let width = 0;
    for (const ch of paragraph) width += /[\u2e80-\uffef]/.test(ch) ? 1 : ch === " " ? 0.3 : 0.56;
    return sum + Math.max(1, Math.ceil(width / perLine));
  }, 0);
}

/** A card is as tall as its words, from a three-line minimum up to a cap; a
 * longer text scrolls in the open editor and is cut with an ellipsis. */
export function humanFrame(card: Pick<HumanCard, "kind"> & { text?: string }) {
  const base = HUMAN_FRAME[card.kind];
  if (!card.text) return base;
  const lines = Math.min(textLines(card.text), CARD_TEXT.maxLines[card.kind]);
  return { width: base.width, height: Math.max(base.height, CARD_TEXT.chrome[card.kind] + lines * CARD_TEXT.line) };
}
/** Lines the card shows before its ellipsis: what its frame holds. */
export const cardTextLines = (card: Pick<HumanCard, "kind" | "text">) =>
  Math.max(1, Math.floor((humanFrame(card).height - CARD_TEXT.chrome[card.kind]) / CARD_TEXT.line));

/** Screen size of the dot a link starts from: about 18 px at any zoom. */
export const connectDotSize = (step: number) => Math.round(Math.max(40, 18 / Math.max(step, 0.01)));

export interface LinkCandidate extends Box { id: string; kind: "human" | "task" | string }
/**
 * The card a link dropped onto. React Flow names it only when the pointer is
 * over a handle that takes links, and task cards take none, so the drop point
 * decides: first what the browser reports under the pointer (topmost card
 * other than the one the link started from), then any card whose box holds
 * the point in flow space — a person's card before a task card, the smaller
 * box first — so a line dropped on a card's edge, label or shadow still lands.
 */
export function resolveLinkTarget(
  source: string,
  stack: ReadonlyArray<string | null | undefined>,
  point: Point | null,
  nodes: ReadonlyArray<LinkCandidate>,
): string | undefined {
  const usable = (id: string | null | undefined): id is string => !!id && id !== source && !id.startsWith("draft-");
  const fromStack = stack.find(usable);
  if (fromStack) return fromStack;
  if (!point) return undefined;
  const hits = nodes.filter((n) => usable(n.id) &&
    point.x >= n.x && point.x <= n.x + n.width && point.y >= n.y && point.y <= n.y + n.height);
  hits.sort((a, b) => Number(b.kind === "human") - Number(a.kind === "human") || a.width * a.height - b.width * b.height);
  return hits[0]?.id;
}

/** Where a new card goes so it is not under the map's own chrome: the point
 * clicked, pulled inside ``area`` (screen px) when the card would cross it. */
export function clearOfChrome(center: Point, size: { width: number; height: number }, area: Box): Point {
  const half = { x: size.width / 2, y: size.height / 2 };
  const clamp = (v: number, lo: number, hi: number) => lo > hi ? (lo + hi) / 2 : Math.min(Math.max(v, lo), hi);
  return {
    x: clamp(center.x, area.x + half.x, area.x + area.width - half.x),
    y: clamp(center.y, area.y + half.y, area.y + area.height - half.y),
  };
}
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

/** Another window wrote the canvas (the live stream says which revision):
 * refetch it unless this window already holds that revision, as the writer
 * does. A map that is not open refetches when it opens. */
export function refreshCanvas(client: QueryClient, sessionId: string, revision: number) {
  const held = client.getQueryData<CanvasRecord>(KEY(sessionId))?.revision ?? -1;
  if (held < revision) void client.invalidateQueries({ queryKey: KEY(sessionId), exact: true });
}

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
