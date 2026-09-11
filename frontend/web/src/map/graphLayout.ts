import type { MapLink } from "./model";
import type { Box, Point } from "./relationGeometry";

type Size = Pick<Box, "width" | "height">;
const COLUMN_GAP = 620;
const ROW_GAP = 440;

/** Larger overviews need more connector space for labels that retain their
 * screen font size. This depends on graph size, never on camera movement. */
const columnGapFor = (count: number) =>
  COLUMN_GAP + Math.min(500, Math.max(0, count - 8) * 60);

type Neighbor = { index: number; weight: number };

/** Isotonic barycenter sweep shared by both layouts: pull nodes toward their
 * connected neighbors without swapping order inside a column or reducing the
 * gap between neighboring cards. Mutates `positions` in place.
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

/** Partition creation order into balanced columns. Dependencies influence the
 * breaks, while later work never jumps back to the left of earlier work.
 */
export function foldLayout(
  ids: string[],
  links: MapLink[],
  sizes: Record<string, Size>,
) {
  if (!ids.length) return {};
  const columnGap = columnGapFor(ids.length);
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
  const adjacent = ids.map(() => [] as Neighbor[]);
  for (const e of edges) {
    children[e.source].push(e.target);
    parents[e.target].push(e.source);
    adjacent[e.source].push({ index: e.target, weight: e.weight });
    adjacent[e.target].push({ index: e.source, weight: e.weight });
  }
  // Cards that hand work to each other sit closer together than cards that
  // merely follow one another in time, so a column reads as groups of related
  // work rather than as an evenly spaced list.
  const linked = new Set(
    edges.filter((e) => e.weight === 1).map((e) => `${Math.min(e.source, e.target)}:${Math.max(e.source, e.target)}`),
  );
  const gapBetween = (above: string, below: string) => {
    const a = index.get(above)!, b = index.get(below)!;
    return linked.has(`${Math.min(a, b)}:${Math.max(a, b)}`) ? ROW_GAP : ROW_GAP * 1.3;
  };
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
      column.forEach((id, i) => {
        positions[id] = { x: x + (widths[col] - sizes[id].width) / 2, y };
        y += sizes[id].height + (i + 1 < column.length ? gapBetween(id, column[i + 1]) : 0);
      });
      x += widths[col] + columnGap;
    });
    sweepColumns(columns, positions, ids, index, sizes, adjacent, gapBetween);
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

const RANK_KINDS = new Set(["dependency", "fanout", "fanin", "continuation"]);

/** Dependency-rank layout for meaningfully connected graphs: rank = longest
 * path from roots over dependency/fanout/fanin/continuation edges, seeded at
 * the chronological frontier so unrelated later work stays right of earlier
 * work. Each rank forms a column; oversized card ranks wrap side by side,
 * branch pills stay together and pack at half the row gap. Within a column
 * creation order is kept, then the shared barycenter sweep aligns neighbors.
 * Fan-in edges return to an earlier card by construction, so backward edges
 * never advance ranks (cycle safe).
 */
export function rankLayout(
  ids: string[],
  links: MapLink[],
  sizes: Record<string, Size>,
) {
  if (!ids.length) return {};
  const columnGap = columnGapFor(ids.length);
  const index = new Map(ids.map((id, i) => [id, i]));
  const inGraph = (e: MapLink) =>
    e.source !== e.target && index.has(e.source) && index.has(e.target);
  const branch = new Set<string>();
  const fanNeighbors = new Map<string, Set<string>>();
  for (const e of links) {
    if (!inGraph(e) || (e.kind !== "fanout" && e.kind !== "fanin")) continue;
    if (e.kind === "fanout") branch.add(e.target);
    else branch.add(e.source);
    (fanNeighbors.get(e.source) ?? fanNeighbors.set(e.source, new Set()).get(e.source)!).add(e.target);
    (fanNeighbors.get(e.target) ?? fanNeighbors.set(e.target, new Set()).get(e.target)!).add(e.source);
  }
  const adjacent = ids.map(() => [] as Neighbor[]);
  const weighted = links
    .filter((e) => e.kind !== "replacement" && inGraph(e))
    .map((e) => ({
      source: index.get(e.source)!,
      target: index.get(e.target)!,
      weight:
        e.kind === "dependency" || e.kind === "continuation"
          ? 1
          : e.kind === "fanout" || e.kind === "fanin"
            ? 0.8
            : 0.4,
    }));
  for (const e of weighted) {
    adjacent[e.source].push({ index: e.target, weight: e.weight });
    adjacent[e.target].push({ index: e.source, weight: e.weight });
  }
  // Longest path from roots. Creation order is topological for recorded
  // dependencies; a backward reference (e.g. a fan-in return) never advances
  // the rank of its earlier target, which also makes cycles harmless.
  const incoming = ids.map(() => [] as number[]);
  for (const e of links) {
    if (!RANK_KINDS.has(e.kind) || !inGraph(e)) continue;
    const source = index.get(e.source)!,
      target = index.get(e.target)!;
    if (source < target) incoming[target].push(source);
  }
  const rank: number[] = [];
  let frontier = -1;
  for (let i = 0; i < ids.length; i++) {
    const r = incoming[i].length
      ? Math.max(...incoming[i].map((parent) => rank[parent])) + 1
      : frontier + 1;
    rank.push(r);
    frontier = Math.max(frontier, r);
  }
  const groups: string[][] = [];
  ids.forEach((id, i) => {
    (groups[rank[i]] ??= []).push(id);
  });
  const rankGroups = groups.filter((group) => group?.length);
  const gapBetween = (above: string, below: string) =>
    branch.has(above) && branch.has(below) ? ROW_GAP / 2 : ROW_GAP;
  const stackHeight = (nodes: string[]) =>
    nodes.reduce(
      (sum, id, i) =>
        sum + sizes[id].height + (i ? gapBetween(nodes[i - 1], id) : 0),
      0,
    );
  const area = ids.reduce(
    (sum, id) => sum + sizes[id].width * sizes[id].height,
    0,
  );
  const unit = Math.sqrt(area / ids.length);
  const cardHeights = ids.filter((id) => !branch.has(id));
  const unitHeight =
    (cardHeights.length ? cardHeights : ids).reduce(
      (sum, id) => sum + sizes[id].height,
      0,
    ) / Math.max(1, cardHeights.length || ids.length);
  const maxCapacity = Math.ceil(Math.sqrt(ids.length) * 1.8);
  let best: Record<string, Point> = {},
    bestScore = Infinity;
  const seen = new Set<string>();
  for (let capacity = 1; capacity <= maxCapacity; capacity++) {
    const targetHeight = capacity * unitHeight + (capacity - 1) * ROW_GAP;
    // Blocks: per rank, runs of equal branch-ness. A branch run stays whole
    // (pills are small); a card run wraps into chunks of the target height.
    const blocks: { nodes: string[]; isBranch: boolean }[] = [];
    for (const group of rankGroups) {
      let run: string[] = [];
      const flushRun = () => {
        if (!run.length) return;
        const isBranch = branch.has(run[0]);
        if (isBranch) blocks.push({ nodes: run, isBranch });
        else {
          let chunk: string[] = [];
          for (const id of run) {
            if (chunk.length && stackHeight([...chunk, id]) > targetHeight) {
              blocks.push({ nodes: chunk, isBranch });
              chunk = [];
            }
            chunk.push(id);
          }
          if (chunk.length) blocks.push({ nodes: chunk, isBranch });
        }
        run = [];
      };
      for (const id of group) {
        if (run.length && branch.has(run[0]) !== branch.has(id)) flushRun();
        run.push(id);
      }
      flushRun();
    }
    // Columns: merge consecutive blocks while they fit, never across a fan
    // boundary and never mixing pills with cards, so a fan keeps its own
    // column and visually leaves and returns to the owning card.
    const columns: string[][] = [];
    const columnKinds: boolean[] = [];
    for (const block of blocks) {
      const column = columns.at(-1);
      const fanBoundary =
        column &&
        block.nodes.some((id) =>
          column.some((other) => fanNeighbors.get(id)?.has(other)),
        );
      if (
        !column ||
        fanBoundary ||
        columnKinds.at(-1) !== block.isBranch ||
        stackHeight([...column, ...block.nodes]) > targetHeight
      ) {
        columns.push([...block.nodes]);
        columnKinds.push(block.isBranch);
      } else column.push(...block.nodes);
    }
    const signature = JSON.stringify(columns);
    if (seen.has(signature)) continue;
    seen.add(signature);
    const widths = columns.map((column) =>
      Math.max(...column.map((id) => sizes[id].width)),
    );
    const heights = columns.map(stackHeight);
    const width =
      widths.reduce((a, b) => a + b, 0) + (columns.length - 1) * columnGap;
    let height = Math.max(...heights);
    const positions: Record<string, Point> = {};
    let x = 0;
    columns.forEach((column, col) => {
      let y = (height - heights[col]) / 2;
      column.forEach((id, i) => {
        positions[id] = { x: x + (widths[col] - sizes[id].width) / 2, y };
        y +=
          sizes[id].height +
          (i + 1 < column.length ? gapBetween(id, column[i + 1]) : 0);
      });
      x += widths[col] + columnGap;
    });
    sweepColumns(columns, positions, ids, index, sizes, adjacent, gapBetween);
    const top = Math.min(...ids.map((id) => positions[id].y));
    for (const id of ids) positions[id].y -= top;
    height = Math.max(...ids.map((id) => positions[id].y + sizes[id].height));
    const length =
      weighted.reduce((sum, e) => {
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
      Math.max(1, weighted.length) /
      unit;
    const score =
      2 * Math.log(width / height / 2.1) ** 2 +
      (0.08 * width * height) / area +
      0.14 * length;
    if (score < bestScore) {
      bestScore = score;
      best = positions;
    }
  }
  return best;
}

/** Same signature as before: chronological fold for legacy sessions, real
 * fork/join geometry once promoted team fans make the graph meaningfully
 * connected (fanout/fanin links present and at least 30% of nodes touch a
 * dependency/fanout/fanin link). Dependency-only histories keep their
 * existing appearance.
 */
export function layoutGraph(
  ids: string[],
  links: MapLink[],
  sizes: Record<string, Size>,
) {
  if (!ids.length) return {};
  const idSet = new Set(ids);
  let fan = false;
  const connected = new Set<string>();
  for (const e of links) {
    if (e.source === e.target || !idSet.has(e.source) || !idSet.has(e.target))
      continue;
    if (e.kind === "fanout" || e.kind === "fanin") fan = true;
    if (e.kind === "dependency" || e.kind === "fanout" || e.kind === "fanin") {
      connected.add(e.source);
      connected.add(e.target);
    }
  }
  return fan && connected.size >= ids.length * 0.3
    ? rankLayout(ids, links, sizes)
    : foldLayout(ids, links, sizes);
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
