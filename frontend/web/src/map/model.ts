import type { BacklogItem } from "../../../core/src/types";

export interface MapTask
  extends Pick<
    BacklogItem,
    "id" | "title" | "objective" | "status" | "deps" | "pending_question"
  > {
  revision?: string;
  content_revision?: string;
  ts?: number;
  started_ts?: number | null;
  finished_ts?: number | null;
  role?: string;
  summary?: string;
  plan_id?: string;
  plan_version?: number;
  attempt?: number;
  superseded_by_plan_id?: string;
  acceptance_check?: string;
}
export interface MapEvent {
  id: string;
  revision?: string;
  item_id: string;
  type: string;
  ts: number;
  text: string;
  role?: string;
  status?: string;
  round_index?: number;
  attempt?: number;
  success?: boolean;
  next_action?: string;
  association?: "explicit" | "single_active_window";
}
export interface Dataset {
  id: string;
  title: string;
  kind: "historical" | "demo" | "synthetic" | "live";
  description: string;
  captured_at?: string;
  read_only: boolean;
  tasks: MapTask[];
  events: MapEvent[];
  cursor?: string;
  incremental?: boolean;
  tasks_complete?: boolean;
  removed_task_ids?: string[];
  reset_history?: boolean;
  history_cursor?: string;
  history_loading?: boolean;
  history_progress?: { loaded_bytes: number; total_bytes: number };
  coverage?: {
    truncated?: boolean;
    note?: string;
    included_tasks?: number;
    source_tasks_read?: number;
  };
}
export type DatasetSummary = Omit<Dataset, "tasks" | "events"> & {
  task_count: number;
  event_count: number;
};
export interface MapLink {
  id: string;
  source: string;
  target: string;
  kind: "dependency" | "replacement" | "context" | "semantic" | "continuation";
  label?: string;
  evidence?: string;
  target_plan_id?: string;
  target_count?: number;
  missing?: boolean;
  cycle?: boolean;
}
export interface MapGraph {
  tasks: MapTask[];
  links: MapLink[];
  missing: number;
  cyclic: boolean;
}

export const ACTIVE = new Set(["running", "in_progress", "claimed"]);
export function statusKey(task: MapTask): string {
  if (task.pending_question) return "question";
  if (ACTIVE.has(task.status)) return "running";
  if (task.status.startsWith("paused") || task.status === "blocked")
    return "paused";
  return [
    "done",
    "failed",
    "aborted",
    "skipped",
    "superseded",
    "pending",
    "missing",
  ].includes(task.status)
    ? task.status
    : "unknown";
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
    objective: "该引用不在当前数据范围内。",
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
  const cyclic = processed !== all.length;
  // Plan replacement links target the earliest visible task in the new plan.
  for (const task of ordered) {
    if (!task.superseded_by_plan_id) continue;
    const targets = ordered.filter(
      (t) => t.id !== task.id && t.plan_id === task.superseded_by_plan_id,
    );
    if (!targets.length) continue;
    links.push({
      id: JSON.stringify(["replacement", task.id, task.superseded_by_plan_id]),
      source: task.id,
      target: targets[0].id,
      kind: "replacement",
      target_plan_id: task.superseded_by_plan_id,
      target_count: targets.length,
    });
  }
  if (cyclic)
    links
      .filter(
        (e) =>
          e.kind === "dependency" &&
          (indegree.get(e.source) ?? 0) > 0 &&
          (indegree.get(e.target) ?? 0) > 0,
      )
      .forEach((e) => {
        e.cycle = true;
      });
  return {
    tasks: all,
    links,
    missing: missing.size,
    cyclic,
  };
}

/** Playback reveals recorded task creation; status replay requires earlier state evidence. */
export function replayTasks(tasks: MapTask[], count: number): MapTask[] {
  return [...tasks]
    .sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0) || a.id.localeCompare(b.id))
    .slice(0, count);
}

/** Connect components with content/context links; recorded dependencies remain authoritative. */
export function connectMap(
  graph: MapGraph,
  semantic: Array<{
    source: string;
    target: string;
    label: string;
    evidence: string;
  }>,
  zh: boolean,
): MapLink[] {
  const links = graph.links.map((link) => {
    const phrase = semantic.find(
      (r) => r.source === link.source && r.target === link.target,
    );
    return phrase && link.kind === "dependency"
      ? {
          ...link,
          label: phrase.label,
          evidence: `${zh ? "执行依赖" : "Execution dependency"} · ${phrase.evidence}`,
        }
      : link;
  });
  const parent = new Map(graph.tasks.map((t) => [t.id, t.id]));
  const find = (id: string): string => {
    const next = parent.get(id)!;
    return next === id ? id : find(next);
  };
  const join = (a: string, b: string) => {
    parent.set(find(a), find(b));
  };
  for (const link of links.filter((l) => l.kind === "dependency"))
    join(link.source, link.target);
  for (const relation of semantic) {
    if (
      !parent.has(relation.source) ||
      !parent.has(relation.target) ||
      graph.tasks.findIndex((t) => t.id === relation.source) >=
        graph.tasks.findIndex((t) => t.id === relation.target) ||
      find(relation.source) === find(relation.target)
    )
      continue;
    links.push({
      ...relation,
      kind: "semantic",
      id: `semantic:${relation.source}:${relation.target}`,
    });
    join(relation.source, relation.target);
  }
  for (let i = 1; i < graph.tasks.length; i++) {
    const task = graph.tasks[i],
      previous = graph.tasks[i - 1];
    if (find(task.id) === find(previous.id)) continue;
    const samePlan = task.plan_id && task.plan_id === previous.plan_id;
    links.push({
      id: `context:${previous.id}:${task.id}`,
      source: previous.id,
      target: task.id,
      kind: "context",
      label: zh
        ? samePlan
          ? "同一计划"
          : "同一研究"
        : samePlan
          ? "Same plan"
          : "Same study",
      evidence: zh
        ? "属于同一研究会话，按时间排列；不表示执行依赖。"
        : "Shared research context, arranged in time; no execution dependency is implied.",
    });
    join(previous.id, task.id);
  }
  return links;
}
