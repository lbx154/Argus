import type { MapLink } from "./model";
import type { Box, Point } from "./relationGeometry";

type Size = Pick<Box, "width" | "height">;
const COLUMN_GAP = 620;
const ROW_GAP = 440;

/** Partition creation order into balanced columns. Dependencies influence the
 * breaks, while later work never jumps back to the left of earlier work.
 */
export function layoutGraph(
  ids: string[],
  links: MapLink[],
  sizes: Record<string, Size>,
) {
  if (!ids.length) return {};
  // Larger overviews need more connector space for labels that retain their
  // screen font size. This depends on graph size, never on camera movement.
  const columnGap =
    COLUMN_GAP + Math.min(500, Math.max(0, ids.length - 8) * 60);
  const index = new Map(ids.map((id, i) => [id, i]));
  const edges = links
    .filter((e) => e.kind !== "replacement" && e.source !== e.target)
    .map((e) => ({
      source: index.get(e.source)!,
      target: index.get(e.target)!,
      weight: e.kind === "dependency" || e.kind === "continuation" ? 1 : 0.4,
    }));
  const children = ids.map(() => [] as number[]);
  const parents = ids.map(() => [] as number[]);
  const adjacent = ids.map(() => [] as { index: number; weight: number }[]);
  for (const e of edges) {
    children[e.source].push(e.target);
    parents[e.target].push(e.source);
    adjacent[e.source].push({ index: e.target, weight: e.weight });
    adjacent[e.target].push({ index: e.source, weight: e.weight });
  }
  const area = ids.reduce(
    (sum, id) => sum + sizes[id].width * sizes[id].height,
    0,
  );
  const unit = Math.sqrt(area / ids.length);
  const averageHeight =
    ids.reduce((sum, id) => sum + sizes[id].height, 0) / ids.length;
  const maxCapacity = Math.ceil(Math.sqrt(ids.length) * 1.8);
  const endingAt = ids.map(() => [] as typeof edges);
  for (const e of edges) endingAt[Math.max(e.source, e.target)].push(e);
  const segmentPenalty = ids.map((_, start) => {
    let penalty = 0;
    return Array.from(
      { length: Math.min(maxCapacity, ids.length - start) + 1 },
      (_, length) => {
        if (length)
          for (const e of endingAt[start + length - 1]) {
            if (Math.min(e.source, e.target) < start) continue;
            if (children[e.source].length > 1 || parents[e.target].length > 1)
              penalty += e.weight * 0.9;
            penalty +=
              Math.max(0, Math.abs(e.target - e.source) - 1) * e.weight;
          }
        return penalty;
      },
    );
  });
  let best: Record<string, Point> = {},
    bestScore = Infinity;
  const seen = new Set<string>();
  for (let capacity = 1; capacity <= maxCapacity; capacity++) {
    const targetHeight = capacity * averageHeight + (capacity - 1) * ROW_GAP;
    const cost = Array(ids.length + 1).fill(Infinity),
      previous = Array(ids.length + 1).fill(0);
    cost[0] = 0;
    for (let end = 1; end <= ids.length; end++) {
      let height = 0;
      for (let start = end - 1; start >= Math.max(0, end - capacity); start--) {
        height += sizes[ids[start]].height + (start === end - 1 ? 0 : ROW_GAP);
        let penalty = segmentPenalty[start][end - start];
        // Avoid cutting a small parallel branch between two adjacent columns.
        if (
          start > 0 &&
          parents[start].some((p) => parents[start - 1].includes(p))
        )
          penalty += 0.7;
        const score =
          cost[start] + 0.2 + (height / targetHeight - 1) ** 2 + penalty;
        if (score < cost[end]) {
          cost[end] = score;
          previous[end] = start;
        }
      }
    }
    const columns: string[][] = [];
    for (let end = ids.length; end > 0; end = previous[end])
      columns.unshift(ids.slice(previous[end], end));
    const signature = JSON.stringify(columns);
    if (seen.has(signature)) continue;
    seen.add(signature);
    const widths = columns.map((column) =>
      Math.max(...column.map((id) => sizes[id].width)),
    );
    const heights = columns.map(
      (column) =>
        column.reduce((sum, id) => sum + sizes[id].height, 0) +
        (column.length - 1) * ROW_GAP,
    );
    const width =
      widths.reduce((a, b) => a + b, 0) + (columns.length - 1) * columnGap;
    let height = Math.max(...heights);
    const positions: Record<string, Point> = {};
    let x = 0;
    columns.forEach((column, col) => {
      let y = (height - heights[col]) / 2;
      for (const id of column) {
        positions[id] = { x: x + (widths[col] - sizes[id].width) / 2, y };
        y += sizes[id].height + ROW_GAP;
      }
      x += widths[col] + columnGap;
    });
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
          offset += sizes[id].height + ROW_GAP;
        });
        for (const block of blocks)
          for (let i = block.start; i <= block.end; i++)
            positions[column[i]].y = offsets[i] + block.sum / block.weight;
      }
    }
    const top = Math.min(...ids.map((id) => positions[id].y));
    for (const id of ids) positions[id].y -= top;
    height = Math.max(...ids.map((id) => positions[id].y + sizes[id].height));
    const length =
      edges.reduce((sum, e) => {
        const a = positions[ids[e.source]],
          b = positions[ids[e.target]];
        return (
          sum +
          Math.hypot(
            b.x +
              sizes[ids[e.target]].width / 2 -
              a.x -
              sizes[ids[e.source]].width / 2,
            b.y +
              sizes[ids[e.target]].height / 2 -
              a.y -
              sizes[ids[e.source]].height / 2,
          ) *
            e.weight
        );
      }, 0) /
      Math.max(1, edges.length) /
      unit;
    const score =
      2 * Math.log(width / height / 2.1) ** 2 +
      (0.08 * width * height) / area +
      0.14 * length +
      (0.08 * cost[ids.length]) / columns.length;
    if (score < bestScore) {
      bestScore = score;
      best = positions;
    }
  }
  return best;
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
