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
  superseded_reason?: string;
  acceptance_check?: string;
  /** 'turn': a single-agent conversation turn that used tools, shown as a card. */
  kind?: string;
  /** Synthesized team branch node (display/navigation only, never a card). */
  branch?: true;
  parent_id?: string;
  team_role?: string;
  excerpt?: string;
  overflow_count?: number;
}
/** One tool call inside a work segment, as the runner reported it. */
export interface WorkStep {
  kind: string;
  label: string;
  ts: number;
  tool?: string;
  status?: string;
  call_id?: string;
}
export interface MapEvent {
  id: string;
  revision?: string;
  item_id: string;
  type: string;
  ts: number;
  text: string;
  /** work.segment: when the segment's last step happened. */
  ts_end?: number;
  /** work.segment: the tool calls that followed the narration. */
  steps?: WorkStep[];
  overflow?: number;
  role?: string;
  status?: string;
  round_index?: number;
  attempt?: number;
  success?: boolean;
  review_skipped?: boolean;
  title?: string;
  team_id?: string;
  team_task_id?: string;
  team_role?: string;
  deps?: string[];
  owner?: string;
  reason?: string;
  width?: number;
  pending_question?: string;
  started_ts?: number | null;
  finished_ts?: number | null;
  updated_ts?: number;
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
  removed_event_ids?: string[];
  team_events_complete?: boolean;
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
  kind:
    | "dependency"
    | "replacement"
    | "context"
    | "semantic"
    | "continuation"
    | "fanout"
    | "fanin";
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
export function attentionTasks(tasks: MapTask[]): MapTask[] {
  return tasks
    .filter((task) => task.status !== "done" && !ACTIVE.has(task.status) &&
      (task.pending_question || task.status === "failed"))
    .sort((a, b) => Number(!!b.pending_question) - Number(!!a.pending_question));
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

/** Iterative SCC traversal also handles histories deeper than the JS call stack. */
function dependencyComponents(children: Map<string, string[]>): Map<string, number> {
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
  if (cyclic) {
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
    cyclic,
  };
}

/** Playback reveals recorded task creation; status replay requires earlier state evidence. */
export function replayTasks(tasks: MapTask[], count: number): MapTask[] {
  return [...tasks]
    .sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0) || a.id.localeCompare(b.id))
    .slice(0, count);
}

/** How many team branches a card may fan out before the rest collapse into one overflow pill. */
export const TEAM_BRANCH_CAP = 16;

/** Local copy of submap's teamTitle: branch titles must not depend on submap.ts. */
function teamBranchTitle(event: MapEvent, zh: boolean): string {
  const route = event.team_task_id?.match(/route-(\d+)/)?.[1];
  const label =
    event.team_role === "idea-route"
      ? zh
        ? "研究路线"
        : "Research route"
      : event.team_role === "idea-review"
        ? zh
          ? "独立复核"
          : "Independent review"
        : event.team_role === "idea-selector"
          ? zh
            ? "方案选择"
            : "Idea selection"
          : "";
  return label
    ? `${label}${route ? ` ${route}` : ""}`
    : event.title || (zh ? "并行子任务" : "Parallel task");
}

/** Short readable excerpt: scientific prose without the runner's control footer. */
function branchExcerpt(text: string | undefined): string {
  const joined = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.replace(/^\s*(?:RESULT|SUMMARY|NEXT_ACTION)\s*=\s*/i, "").trim())
    .filter(
      (line) =>
        line &&
        !/^(?:Decision\s*:|(?:MILESTONE_STATUS|NEXT_OWNER|OPERATOR_QUESTION|OPERATOR_OPTIONS)\s*=)/i.test(
          line,
        ),
    )
    .join(" ");
  return joined.length > 160 ? `${joined.slice(0, 159)}…` : joined;
}

/** Promote each task's `team.task` events into lightweight branch nodes so a
 * parallel portfolio fans out of its owning card and returns to it, instead of
 * hiding as steps inside the card. Pure: the input graph is never mutated, and
 * it is returned unchanged (same identity) when nothing is promoted.
 * - node id = team event id (already a globally unique "team:…" digest);
 *   an id that collides with an existing task is skipped, never redefined.
 * - parent task → each root team node: kind "fanout".
 * - team dependency edges (already in team-event-id space): kind "fanout".
 * - terminal team nodes (no team children, e.g. the idea-selector) → parent
 *   task: kind "fanin", so the fan visually closes on the owning card.
 * - more than TEAM_BRANCH_CAP nodes: keep the first by ts and add a single
 *   "+N more" overflow node that opens the parent card.
 */
/** Latest declared fan width per owning task. The planner announces how wide
 * a formation is meant to be ("idea.portfolio.formed" carries `width`) before
 * every branch has spawned; cards show it so intent reads ahead of reality. */
export function formationWidths(events: MapEvent[]): Map<string, number> {
  const latest = new Map<string, { ts: number; width: number }>();
  for (const event of events) {
    if (event.type !== "idea.portfolio.formed") continue;
    const width = Number(event.width);
    if (!Number.isInteger(width) || width <= 0) continue;
    const previous = latest.get(event.item_id);
    if (!previous || event.ts >= previous.ts)
      latest.set(event.item_id, { ts: event.ts, width });
  }
  return new Map([...latest].map(([id, entry]) => [id, entry.width]));
}

export function promoteTeamBranches(
  graph: MapGraph,
  events: MapEvent[],
  zh: boolean,
): MapGraph {
  const byParent = new Map<string, Map<string, MapEvent>>();
  for (const event of events) {
    if (event.type !== "team.task" || !event.item_id) continue;
    const bucket = byParent.get(event.item_id) ?? new Map<string, MapEvent>();
    bucket.set(event.id, event); // the latest revision of an event wins
    byParent.set(event.item_id, bucket);
  }
  if (!byParent.size) return graph;
  const taken = new Set(graph.tasks.map((task) => task.id));
  const branches: MapTask[] = [];
  const links: MapLink[] = [];
  for (const task of graph.tasks) {
    if (task.branch) continue;
    const own = [...(byParent.get(task.id)?.values() ?? [])]
      .filter((event) => !taken.has(event.id))
      .sort((a, b) => a.ts - b.ts || a.id.localeCompare(b.id));
    if (!own.length) continue;
    const kept = own.slice(0, TEAM_BRANCH_CAP);
    const keptIds = new Set(kept.map((event) => event.id));
    for (const event of kept) {
      taken.add(event.id);
      branches.push({
        id: event.id,
        title: teamBranchTitle(event, zh),
        objective: branchExcerpt(event.text),
        excerpt: branchExcerpt(event.text),
        status: event.status || "unknown",
        deps: [],
        pending_question: event.pending_question,
        role: "team",
        team_role: event.team_role,
        ts: event.ts,
        branch: true,
        parent_id: task.id,
      });
    }
    const hasChild = new Set(
      kept.flatMap((event) =>
        (event.deps ?? []).filter((dep) => keptIds.has(dep) && dep !== event.id),
      ),
    );
    for (const event of kept) {
      const deps = [...new Set(event.deps ?? [])].filter(
        (dep) => keptIds.has(dep) && dep !== event.id,
      );
      if (deps.length)
        for (const dep of deps)
          links.push({
            id: JSON.stringify(["fanout", dep, event.id]),
            source: dep,
            target: event.id,
            kind: "fanout",
          });
      else
        links.push({
          id: JSON.stringify(["fanout", task.id, event.id]),
          source: task.id,
          target: event.id,
          kind: "fanout",
        });
      if (!hasChild.has(event.id))
        links.push({
          id: JSON.stringify(["fanin", event.id, task.id]),
          source: event.id,
          target: task.id,
          kind: "fanin",
        });
    }
    const dropped = own.length - kept.length;
    const overflowId = `team-overflow:${task.id}`;
    if (dropped > 0 && !taken.has(overflowId)) {
      taken.add(overflowId);
      branches.push({
        id: overflowId,
        title: zh ? `还有 ${dropped} 条` : `+${dropped} more`,
        objective: zh
          ? "更多并行子任务收录在所属任务卡片中。"
          : "The remaining parallel subtasks live inside the owning card.",
        status: "recorded",
        deps: [],
        role: "team",
        ts: own[TEAM_BRANCH_CAP]?.ts,
        branch: true,
        parent_id: task.id,
        overflow_count: dropped,
      });
      links.push(
        {
          id: JSON.stringify(["fanout", task.id, overflowId]),
          source: task.id,
          target: overflowId,
          kind: "fanout",
        },
        {
          id: JSON.stringify(["fanin", overflowId, task.id]),
          source: overflowId,
          target: task.id,
          kind: "fanin",
        },
      );
    }
  }
  if (!branches.length) return graph;
  return {
    ...graph,
    tasks: [...graph.tasks, ...branches],
    links: [...graph.links, ...links],
  };
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
