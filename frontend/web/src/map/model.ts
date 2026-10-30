import type { BacklogItem } from "../../../core/src/types";

export interface MapTask
  extends Pick<
    BacklogItem,
    "id" | "title" | "status" | "deps" | "latest_action" | "result_paths"
  > {
  ts?: number;
  started_ts?: number | null;
  finished_ts?: number | null;
  role?: string;
  turn_kind?: 'qa';
}
/** Canvas summaries of recorded letters. Explicit readers request the original by id. */
export interface MapEvent {
  id: string;
  item_id: string;
  type: 'handoff' | 'turn.asked' | 'turn.replied';
  ts: number;
  /** A short heading for the drawing, never the historical body. */
  text: string;
  role?: string;
  to?: string;
}
export interface Dataset {
  id: string;
  tasks: MapTask[];
  events: MapEvent[];
  /** The next recorded line to request when following map history. */
  cursor?: string;
}
export interface MapLink {
  id: string;
  source: string;
  target: string;
  kind:
    | "dependency"
    | "context"
    | "continuation";
  label?: string;
  evidence?: string;
  /** The label says what connects these two tasks, not just what kind of
   * line this is. Only a stated label is written on the map at rest. */
  stated?: boolean;
  missing?: boolean;
  cycle?: boolean;
}
export interface MapGraph {
  tasks: MapTask[];
  links: MapLink[];
  missing: number;
}

export const ACTIVE = new Set(["running", "in_progress", "claimed"]);

/** The newest moment work demonstrably moved on: a later card finished or is running. */
function latestProgressTs(tasks: MapTask[]): number {
  return tasks.reduce((latest, task) => (
    (task.status === "done" || ACTIVE.has(task.status)) && (task.ts ?? 0) > latest ? (task.ts ?? 0) : latest
  ), 0);
}

/** A failed card the pipeline has since moved past: later work finished or is running. */
export function supersededByLaterWork(task: MapTask, tasks: MapTask[]): boolean {
  return task.status === "failed" && (task.ts ?? 0) < latestProgressTs(tasks);
}

export function attentionTasks(tasks: MapTask[]): MapTask[] {
  // An open question always needs the reader. A failure does only while it is
  // the frontier: once a later card has finished or is running, the Planner
  // has already routed around it (the trial's first card was archived as
  // failed after a restart and stayed "needs attention" for the whole run
  // while three later tasks completed, 2026-09-16).
  // Nor does a step the operator aborted, one a newer plan replaced, or a
  // housekeeping step on Argus's own records (.argus/…): none of these is
  // something the reader has to act on (rule 6, s-7d7b7d22 2026-10-24).
  return tasks
    .filter(task => task.status === 'blocked' ||
      (task.status === 'failed' && !supersededByLaterWork(task, tasks)))
    .sort((a, b) => Number(b.status === 'blocked') - Number(a.status === 'blocked'));
}

export function currentTask(tasks: MapTask[]): MapTask | undefined {
  return tasks.find((task) => ACTIVE.has(task.status)) ??
    attentionTasks(tasks)[0] ??
    tasks.find((task) => task.status === "pending") ??
    tasks.at(-1);
}

export function taskDependencies(graph: MapGraph, taskId: string) {
  const upstream = new Set<string>();
  const downstream = new Set<string>();
  for (const link of graph.links) {
    if (link.kind !== "dependency") continue;
    if (link.target === taskId) upstream.add(link.source);
    if (link.source === taskId) downstream.add(link.target);
  }
  return {
    upstream: graph.tasks.filter((task) => upstream.has(task.id)),
    downstream: graph.tasks.filter((task) => downstream.has(task.id)),
  };
}

export function statusKey(task: MapTask): string {
  if (task.status === "cancelled") return "aborted";
  if (task.status === 'blocked') return "question";
  if (ACTIVE.has(task.status)) return "running";
  return [
    "done",
    "failed",
    "aborted",
    "skipped",
    "pending",
    "missing",
  ].includes(task.status)
    ? task.status
    : "unknown";
}

/** Iterative SCC traversal also handles histories deeper than the JS call stack. */
export function dependencyComponents(children: Map<string, string[]>): Map<string, number> {
  const seen = new Set<string>();
  const finished: string[] = [];
  const parents = new Map([...children.keys()].map((id) => [id, [] as string[]]));
  for (const [id, next] of children)
    for (const child of next) parents.get(child)!.push(id);
  for (const id of children.keys()) {
    const stack: Array<[string, boolean]> = [[id, false]];
    while (stack.length) {
      const [node, exiting] = stack.pop()!;
      if (exiting) finished.push(node);
      else if (!seen.has(node)) {
        seen.add(node);
        stack.push([node, true]);
        for (const child of children.get(node)!)
          if (!seen.has(child)) stack.push([child, false]);
      }
    }
  }
  const component = new Map<string, number>();
  for (const id of finished.reverse()) {
    if (component.has(id)) continue;
    const group = component.size;
    const stack = [id];
    while (stack.length) {
      const node = stack.pop()!;
      if (component.has(node)) continue;
      component.set(node, group);
      for (const parent of parents.get(node)!) stack.push(parent);
    }
  }
  return component;
}

/** Preserve recorded dependencies and plan changes; chronology is a separate shared context. */
export function buildMap(tasks: MapTask[]): MapGraph {
  const unique = new Map(tasks.map((task) => [task.id, task]));
  const ordered = [...unique.values()].sort(
    (a, b) => (a.ts ?? 0) - (b.ts ?? 0) || a.id.localeCompare(b.id),
  );
  const links: MapLink[] = [];
  const missing = new Set<string>();
  for (const task of ordered)
    for (const dep of new Set(task.deps ?? [])) {
      if (!unique.has(dep)) missing.add(dep);
      links.push({
        id: JSON.stringify(["dep", dep, task.id]),
        source: dep,
        target: task.id,
        kind: "dependency",
        missing: !unique.has(dep),
      });
    }
  const stubs: MapTask[] = [...missing].map((id) => ({
    id,
    title: "未包含的依赖",
    status: "missing",
    deps: [],
    role: "system",
  }));
  const all = [...stubs, ...ordered];
  const indegree = new Map(all.map((t) => [t.id, 0]));
  const children = new Map(all.map((t) => [t.id, [] as string[]]));
  links.forEach((e) => {
    indegree.set(e.target, (indegree.get(e.target) ?? 0) + 1);
    children.get(e.source)?.push(e.target);
  });
  const queue = all.filter((t) => indegree.get(t.id) === 0).map((t) => t.id);
  let processed = 0;
  for (let index = 0; index < queue.length; index++) {
    const id = queue[index];
    processed++;
    for (const child of children.get(id) ?? []) {
      indegree.set(child, indegree.get(child)! - 1);
      if (indegree.get(child) === 0) queue.push(child);
    }
  }
  if (processed !== all.length) {
    // Kahn's residual includes work blocked downstream of a cycle. Only an
    // edge inside one strongly connected component actually belongs to a cycle.
    const component = dependencyComponents(children);
    links
      .filter(
        (e) =>
          e.kind === "dependency" &&
          component.get(e.source) === component.get(e.target),
      )
      .forEach((e) => {
        e.cycle = true;
      });
  }
  return {
    tasks: all,
    links,
    missing: missing.size,
  };
}

/** Playback reveals recorded task creation; status replay requires earlier state evidence. */
export function replayTasks(tasks: MapTask[], count: number): MapTask[] {
  return [...tasks]
    .sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0) || a.id.localeCompare(b.id))
    .slice(0, count);
}

/** A person's canvas card id ("hc-35885033b637") never reaches a title: the
 * projection says it as the step's number and words (map_view.plain_card_ids);
 * a title written before that, or by a model, drops the id. */
export function plainTitle(title: string): string {
  if (!title.includes("hc-")) return title;
  return title.replace(/(^|[^0-9A-Za-z_-])\s*hc-[0-9A-Za-z_-]{4,}(?![0-9A-Za-z_-])/g, "$1")
    .replace(/[ \t]{2,}/g, " ").trim();
}
