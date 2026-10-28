import { useCallback, useRef } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { ApiError } from "../../../core/src/http";
import { api } from "../api";
import { MAP_CAPABILITY, useCapability } from "../lib/capabilities";

/** What a person placed on the map: their cards, their links, and every card
 * they moved. Argus's own cards are not stored here; a task card with a
 * position here keeps it and is never laid out again (experience rule 5). */
export interface CanvasRecord {
  version: number;
  revision: number;
  cards: HumanCard[];
  links: HumanLink[];
  positions: Record<string, Point>;
  /** The person's required steps (必经步骤) by number, each with where it
   * stands; the server reads it from the backlog on every response. */
  route?: RouteView;
}
/** Where a required step stands: 未开始 / 进行中 / 等你看 / 已完成 / 有疑问. */
export type StepStatus = "todo" | "running" | "review" | "done" | "question";
export interface RouteStep {
  id: string;
  /** Place along the 先后 chain from 1; null for a step that may come any time. */
  number: number | null;
  text: string;
  checkpoint: boolean;
  after: string[];
  status: StepStatus;
  /** The tasks that fulfil this step. */
  task_ids: string[];
  /** The question card waiting on the person about this step, if any. */
  question_item_id: string;
}
export interface RouteView { steps: RouteStep[]; done: number; total: number }
export interface Point { x: number; y: number }
export interface Box extends Point { width: number; height: number }
export interface CanvasAttachment {
  attachment_id: string;
  name: string;
  relative_path: string;
  mime: string;
  size_bytes: number;
}
export type HumanCardKind = "idea" | "file" | "step";
export interface HumanCard {
  id: string;
  kind: HumanCardKind;
  text: string;
  attachment: CanvasAttachment | null;
  /** A step that pauses for the person once its work is delivered. */
  checkpoint?: boolean;
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

export const STEP_STATUS: Record<StepStatus, [string, string]> = {
  todo: ["未开始", "Not started"],
  running: ["进行中", "In progress"],
  review: ["等你看", "Waiting for you"],
  done: ["已完成", "Done"],
  question: ["有疑问", "A question for you"],
};
const CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳";
/** ① ② ③ for a numbered step; "" for one that may come any time. */
export const stepMark = (n: number | null | undefined) =>
  n == null ? "" : n >= 1 && n <= CIRCLED.length ? CIRCLED[n - 1] : `(${n})`;
/** Flow-space size of a person's card; task cards are ~1440 wide, so these
 * read at the same zoom without covering a task. An idea and a required step
 * are one family and one width: wide enough that the person's words read at
 * overview zoom (a step was 880 wide, ~110 px there, and cut "写 wordstat/co…"). */
export const HUMAN_FRAME = {
  idea: { width: 1200, height: 480 }, file: { width: 880, height: 360 }, step: { width: 1200, height: 480 },
} as const;
/** Type metrics of a person's card body (canvas.css), in flow units; a step
 * carries its number and status in the header row. */
export const CARD_TEXT = {
  font: 60, line: 87, pad: 64,
  chrome: { idea: 210, file: 330, step: 240 }, maxLines: { idea: 12, file: 6, step: 8 },
} as const;
/** The far view (CanvasNodes farLabelFont): the words as a large label sized
 * for the screen, whole lines up to ``lines``. ``font`` is the label size at
 * the usual overview zoom, which a step's frame is made tall enough for. */
export const FAR_TEXT = { pad: 28, font: 112, line: 1.2, lines: 4 } as const;

/** Text units (a CJK character is about one em wide, Latin about half) in
 * each paragraph of ``text``. */
function textUnits(text: string): number[] {
  return text.split("\n").map((paragraph) => {
    let width = 0;
    for (const ch of paragraph) width += /[\u2e80-\uffef]/.test(ch) ? 1 : ch === " " ? 0.3 : 0.56;
    return width;
  });
}

/** Lines the words take at ``font`` in a measure ``width`` wide; each
 * paragraph starts a line. */
export function textLines(text: string, width: number = HUMAN_FRAME.idea.width - 2 * CARD_TEXT.pad,
  font: number = CARD_TEXT.font): number {
  // One em short of the measure, so a line that wraps early still fits.
  const perLine = Math.max(1, Math.floor(width / font) - 1);
  return textUnits(text).reduce((sum, units) => sum + Math.max(1, Math.ceil(units / perLine)), 0);
}

/** A card is as tall as its words, from a three-line minimum up to a cap; a
 * longer text scrolls in the open editor and is cut with an ellipsis. A step
 * is also tall enough for its words in the far view (number and status in a
 * row above them, up to FAR_TEXT.lines whole lines). */
export function humanFrame(card: Pick<HumanCard, "kind"> & { text?: string }) {
  const base = HUMAN_FRAME[card.kind];
  if (!card.text) return base;
  const lines = Math.min(textLines(card.text, base.width - 2 * CARD_TEXT.pad), CARD_TEXT.maxLines[card.kind]);
  let height = Math.max(base.height, CARD_TEXT.chrome[card.kind] + lines * CARD_TEXT.line);
  if (card.kind === "step") {
    const far = Math.min(FAR_TEXT.lines, textLines(card.text, base.width - 2 * FAR_TEXT.pad * 1.6, FAR_TEXT.font));
    height = Math.max(height, Math.ceil(2 * FAR_TEXT.pad + stepHeadHeight(FAR_TEXT.font) + far * FAR_TEXT.line * FAR_TEXT.font));
  }
  return { width: base.width, height };
}
/** The far view's header row of a step (badge, dot and word) at label size ``font``. */
export const stepHeadHeight = (font: number) => Math.ceil(font * 1.2);
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
    enabled: useCapability(MAP_CAPABILITY) && enabled,
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
  const addCard = useCallback((card: { kind: HumanCard["kind"]; text?: string; attachment?: CanvasAttachment; position: Point; checkpoint?: boolean }) =>
    write<{ canvas: CanvasRecord; card: HumanCard }>("POST", () => "/cards", (revision) => ({ revision, ...card })), [write]);
  const editCard = useCallback((id: string, text: string) =>
    write<{ canvas: CanvasRecord; card: HumanCard }>("PATCH", () => `/cards/${encodeURIComponent(id)}`, (revision) => ({ revision, text })), [write]);
  /** 设为必经步骤 / 改回想法, and a step's "到这步先停下来给我看". */
  const patchCard = useCallback((id: string, change: { kind?: "idea" | "step"; checkpoint?: boolean }) =>
    write<{ canvas: CanvasRecord; card: HumanCard }>("PATCH", () => `/cards/${encodeURIComponent(id)}`, (revision) => ({ revision, ...change })), [write]);
  /** Step statuses follow the tasks: refetch when the tasks moved. */
  const refreshRoute = useCallback(() => {
    if (client.getQueryData<CanvasRecord>(KEY(sessionId))?.route?.total)
      void client.invalidateQueries({ queryKey: KEY(sessionId), exact: true });
  }, [client, sessionId]);
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
  return { canvas, ready: query.isSuccess, addCard, editCard, patchCard, refreshRoute, deleteCard, addLink, setLinkKind, deleteLink, move };
}
export type CanvasApi = ReturnType<typeof useCanvas>;
