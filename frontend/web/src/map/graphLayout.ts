import { dependencyComponents, type MapLink } from "./model";
import type { Box, Point } from "./relationGeometry";

type Size = Pick<Box, "width" | "height">;
const COLUMN_GAP = 620;
const ROW_GAP = 440;

/** Larger overviews need more connector space for labels that retain their
 * screen font size. This depends on graph size, never on camera movement. */
const columnGapFor = (count: number) =>
  COLUMN_GAP + Math.min(500, Math.max(0, count - 8) * 60);

type Neighbor = { index: number; weight: number };

/** Isotonic barycenter sweep: pull nodes toward their connected neighbors
 * without swapping order inside a column or reducing the gap between
 * neighboring cards. Mutates `positions` in place.
 */
function sweepColumns(
  columns: string[][],
  positions: Record<string, Point>,
  ids: string[],
  index: Map<string, number>,
  sizes: Record<string, Size>,
  adjacent: Neighbor[][],
  gapBetween: (above: string, below: string) => number,
) {
  const anchors = new Map(
    ids.map((id) => [id, positions[id].y + sizes[id].height / 2]),
  );
  for (let sweep = 0; sweep < 6; sweep++) {
    for (const column of sweep % 2 ? [...columns].reverse() : columns) {
      const members = new Set(column);
      const offsets: number[] = [];
      const blocks: {
        start: number;
        end: number;
        sum: number;
        weight: number;
      }[] = [];
      let offset = 0;
      column.forEach((id, i) => {
        let center = anchors.get(id)! * 0.8,
          weight = 0.8;
        for (const neighbor of adjacent[index.get(id)!]) {
          const other = ids[neighbor.index];
          if (members.has(other)) continue;
          center +=
            (positions[other].y + sizes[other].height / 2) * neighbor.weight;
          weight += neighbor.weight;
        }
        offsets.push(offset);
        const wanted = center / weight - sizes[id].height / 2 - offset;
        blocks.push({ start: i, end: i, sum: wanted * weight, weight });
        // Isotonic compaction follows connected nodes without swapping task
        // order or reducing the minimum gap between neighboring cards.
        while (blocks.length > 1) {
          const a = blocks[blocks.length - 2],
            b = blocks[blocks.length - 1];
          if (a.sum / a.weight <= b.sum / b.weight) break;
          a.end = b.end;
          a.sum += b.sum;
          a.weight += b.weight;
          blocks.pop();
        }
        offset +=
          sizes[id].height +
          (i + 1 < column.length ? gapBetween(id, column[i + 1]) : ROW_GAP);
      });
      for (const block of blocks)
        for (let i = block.start; i <= block.end; i++)
          positions[column[i]].y = offsets[i] + block.sum / block.weight;
    }
  }
}

/** Execution dependencies define columns: each task is one column beyond
 * its latest prerequisite. Independent tasks share a column, in creation
 * order. A recorded cycle occupies one column so its downstream work can
 * still be placed; the cycle's own arrows remain visible. */
export function layoutGraph(
  ids: string[],
  links: MapLink[],
  sizes: Record<string, Size>,
) {
  if (!ids.length) return {};
  const columnGap = columnGapFor(ids.length);
  const index = new Map(ids.map((id, i) => [id, i]));
  // Continuations are pages of one task, rather than dependencies invented
  // between tasks. Other recorded relationships do not gate execution.
  const edges = links.filter((e) => e.kind === "dependency" || e.kind === "continuation");
  const children = new Map(ids.map((id) => [id, [] as string[]]));
  const adjacent = ids.map(() => [] as Neighbor[]);
  for (const e of edges) {
    children.get(e.source)!.push(e.target);
    adjacent[index.get(e.source)!].push({ index: index.get(e.target)!, weight: 1 });
    adjacent[index.get(e.target)!].push({ index: index.get(e.source)!, weight: 1 });
  }
  const component = dependencyComponents(children);
  const groups = new Set(component.values());
  const next = new Map([...groups].map((id) => [id, new Set<number>()]));
  const indegree = new Map([...groups].map((id) => [id, 0]));
  const rank = new Map([...groups].map((id) => [id, 0]));
  for (const e of edges) {
    const source = component.get(e.source)!, target = component.get(e.target)!;
    if (source === target || next.get(source)!.has(target)) continue;
    next.get(source)!.add(target);
    indegree.set(target, indegree.get(target)! + 1);
  }
  const queue = [...groups].filter((id) => indegree.get(id) === 0);
  for (let i = 0; i < queue.length; i++) {
    const source = queue[i];
    for (const target of next.get(source)!) {
      rank.set(target, Math.max(rank.get(target)!, rank.get(source)! + 1));
      indegree.set(target, indegree.get(target)! - 1);
      if (indegree.get(target) === 0) queue.push(target);
    }
  }
  const columns: string[][] = [];
  for (const id of ids) {
    const column = rank.get(component.get(id)!)!;
    (columns[column] ??= []).push(id);
  }
  const widths = columns.map((column) => Math.max(...column.map((id) => sizes[id].width)));
  const heights = columns.map((column) => column.reduce((sum, id) => sum + sizes[id].height, 0) + (column.length - 1) * ROW_GAP);
  const height = Math.max(...heights);
  const positions: Record<string, Point> = {};
  let x = 0;
  columns.forEach((column, col) => {
    let y = (height - heights[col]) / 2;
    for (const id of column) {
      positions[id] = { x, y };
      y += sizes[id].height + ROW_GAP;
    }
    x += widths[col] + columnGap;
  });
  sweepColumns(columns, positions, ids, index, sizes, adjacent, () => ROW_GAP);
  const top = Math.min(...ids.map((id) => positions[id].y));
  for (const id of ids) positions[id].y -= top;
  return positions;
}

/** Lane index per edge: how many earlier edges share this edge's source or
 * target. One pass over the list; a preceding edge sharing both endpoints
 * counts once, exactly as the original prefix filter did.
 */
export function edgeLanes(
  links: readonly { source: string; target: string }[],
): number[] {
  const bySource = new Map<string, number>();
  const byTarget = new Map<string, number>();
  const byPair = new Map<string, number>();
  return links.map((link) => {
    const pair = `${link.source}\u0000${link.target}`;
    const lane =
      (bySource.get(link.source) ?? 0) +
      (byTarget.get(link.target) ?? 0) -
      (byPair.get(pair) ?? 0);
    bySource.set(link.source, (bySource.get(link.source) ?? 0) + 1);
    byTarget.set(link.target, (byTarget.get(link.target) ?? 0) + 1);
    byPair.set(pair, (byPair.get(pair) ?? 0) + 1);
    return lane;
  });
}

/** Choose facing ports, including recorded backward and self-referencing edges. */
export function relationPorts(a: Box, b: Box) {
  if (a.x === b.x && a.y === b.y)
    return { sourceHandle: "right", targetHandle: "bottom" };
  const dx = b.x + b.width / 2 - a.x - a.width / 2;
  const dy = b.y + b.height / 2 - a.y - a.height / 2;
  const verticalGap = dy > 0 ? b.y - a.y - a.height : a.y - b.y - b.height;
  const horizontalGap = dx > 0 ? b.x - a.x - a.width : a.x - b.x - b.width;
  if (verticalGap >= 0 && horizontalGap < 0)
    return dy >= 0
      ? { sourceHandle: "bottom", targetHandle: "top" }
      : { sourceHandle: "top", targetHandle: "bottom" };
  return dx >= 0
    ? { sourceHandle: "right", targetHandle: "left" }
    : { sourceHandle: "left", targetHandle: "right" };
}
