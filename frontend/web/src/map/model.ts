import { parkReason } from "../../../core/src/missionOutcome";
import type { BacklogItem } from "../../../core/src/types";

export interface MapTask
  extends Pick<
    BacklogItem,
    "id" | "title" | "objective" | "status" | "deps" | "pending_question"
  > {
  revision?: string;
  content_revision?: string;
  /** The backlog's last recorded error, e.g. why a stage was held. */
  last_error?: string;
  /** Why a ``parked`` task is waiting. */
  park_reason?: string;
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
  goal_contribution?: string;
  plan_hypothesis?: string;
  non_goals?: string[];
  outcome?: Partial<NonNullable<BacklogItem['outcome']>>;
  /** 'turn': a single-agent answer or action, shown as a card. */
  kind?: string;
  turn_kind?: 'qa';
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
  tool_details_recorded?: boolean;
  overflow?: number;
  role?: string;
  status?: string;
  /** Why a ``parked`` mission paused; older ``paused_*`` statuses name it themselves. */
  park_reason?: string;
  /** Who or what stopped this work short of a failure, as the record names it. */
  stop_kind?: string;
  round_index?: number;
  attempt?: number;
  success?: boolean;
  review_skipped?: boolean;
  backend_unavailable?: boolean;
  outcome?: MapTask["outcome"];
  overall_complete?: boolean;
  campaign_continues?: boolean;
  stage_certification?: string;
  title?: string;
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
    | "continuation";
  label?: string;
  evidence?: string;
  /** The label says what connects these two tasks, not just what kind of
   * line this is. Only a stated label is written on the map at rest. */
  stated?: boolean;
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

/** Resolve from merged events, not a page-local task snapshot or an earlier attempt. */
export function latestMissionCompletion(task: MapTask, events: MapEvent[]): MapEvent | undefined {
  let latest: MapEvent | undefined;
  for (const event of events) {
    if (event.item_id !== task.id || ![
      "life.mission.started", "life.mission.completed", "life.mission.failed", "life.mission.orphaned",
    ].includes(event.type)) continue;
    if (!latest || event.ts >= latest.ts) latest = event;
  }
  if (latest?.type !== "life.mission.completed") return undefined;
  // Settlement is recorded after the backlog finish timestamp. History pages
  // may not yet contain the current attempt's completion.
  if (latest.ts < Math.max(task.started_ts || 0, task.finished_ts || 0)) return undefined;
  if (latest.attempt != null && task.attempt != null && latest.attempt !== task.attempt) return undefined;
  return latest;
}

/** The newest moment work demonstrably moved on: a later card finished or is running. */
function latestProgressTs(tasks: MapTask[]): number {
  return tasks.reduce((latest, task) => (
    (task.status === "done" || ACTIVE.has(task.status)) && (task.ts ?? 0) > latest ? (task.ts ?? 0) : latest
  ), 0);
}

/** A failed card the pipeline has since moved past: later work finished or is running. */
export function supersededByLaterWork(task: MapTask, tasks: MapTask[]): boolean {
  return task.status === "failed" && !task.pending_question && (task.ts ?? 0) < latestProgressTs(tasks);
}

export function attentionTasks(tasks: MapTask[]): MapTask[] {
  // An open question always needs the reader. A failure does only while it is
  // the frontier: once a later card has finished or is running, the Planner
  // has already routed around it (the trial's first card was archived as
  // failed after a restart and stayed "needs attention" for the whole run
  // while three later tasks completed, 2026-09-16).
  return tasks
    .filter((task) => task.status !== "done" && !ACTIVE.has(task.status) &&
      (task.pending_question || (task.status === "failed" && !supersededByLaterWork(task, tasks))))
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
  if (task.status === "cancelled") return "aborted";
  if (task.pending_question) return "question";
  if (ACTIVE.has(task.status)) return "running";
  if (task.outcome?.review_status === "unavailable") return "review_unavailable";
  // The Manager kept the stage open after a reviewed attempt: the work is
  // waiting on the next step, not broken. The backlog still records it as
  // failed so retry accounting is unchanged; only the reading differs.
  if (task.status === "failed" && isStageHold(task)) return "held";
  if (parkReason(task.status) !== null || task.status === "blocked")
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

/** The Manager kept this stage open. Read from the current outcome when a
 * terminal event bound one; otherwise from the backlog row's own error, which
 * the settlement writes in the same update that sets ``failed`` and so always
 * describes the current attempt. */
export function isStageHold(task: MapTask): boolean {
  if (task.outcome?.stage_certification === "not_certified") return true;
  return !task.outcome?.execution_status && /^manager stage hold:/i.test(task.last_error || "");
}

/** The project's concluding acceptance, if it still is one. A certification
 * says "the project is done" only until work starts again: once any mission
 * starts after it (a new goal, an operator redirect, a retry of the same task),
 * the earlier acceptance describes a past state and must not headline the map. */
export function latestCertifiedTask(tasks: MapTask[], events: MapEvent[]): MapTask | undefined {
  const reopenedAfter = (ts: number) => events.some((event) =>
    event.type === "life.mission.started" && event.ts > ts);
  return tasks.filter(task => task.status === "done"
    && task.outcome?.review_status === "done"
    && task.outcome.stage_certification === "certified")
    .map(task => ({ task, completion: latestMissionCompletion(task, events) }))
    .filter(({ completion }) => completion?.overall_complete === true && !reopenedAfter(completion.ts))
    .map(({ task }) => task)
    .sort((a, b) => (b.finished_ts ?? b.ts ?? 0) - (a.finished_ts ?? a.ts ?? 0))[0];
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

export type LineNote = { source: string; target: string; label: string };

/** The pairs of tasks whose line has nothing to say yet beyond its kind: the
 * lines a note is asked for (useMapLines). */
export function unstatedPairs(links: MapLink[]): Array<{ source: string; target: string }> {
  return links
    .filter((link) => !link.stated && (link.kind === "context" || (link.kind === "dependency" && !link.label)))
    .map(({ source, target }) => ({ source, target }));
}

/** Connect components with content/context links; recorded dependencies remain authoritative.
 *
 * `notes` say what an existing line carries. They annotate the line between
 * their two tasks and never add one or change its kind, so the map keeps its
 * shape whether or not the notes have arrived. */
export function connectMap(
  graph: MapGraph,
  semantic: Array<{
    source: string;
    target: string;
    label: string;
  }>,
  zh: boolean,
  notes: LineNote[] = [],
): MapLink[] {
  const noteFor = (source: string, target: string) =>
    notes.find((n) => n.source === source && n.target === target && n.label);
  const links = graph.links.map((link): MapLink => {
    if (link.kind !== "dependency") return link;
    const phrase =
      semantic.find((r) => r.source === link.source && r.target === link.target) ??
      noteFor(link.source, link.target);
    return phrase
      ? {
          ...link,
          label: phrase.label,
          stated: true,
          evidence: `${zh ? "执行依赖" : "Execution dependency"} · ${phrase.label}`,
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
      source: relation.source,
      target: relation.target,
      label: relation.label,
      kind: "semantic",
      stated: true,
      id: `semantic:${relation.source}:${relation.target}`,
    });
    join(relation.source, relation.target);
  }
  for (let i = 1; i < graph.tasks.length; i++) {
    const task = graph.tasks[i],
      previous = graph.tasks[i - 1];
    if (find(task.id) === find(previous.id)) continue;
    const samePlan = task.plan_id && task.plan_id === previous.plan_id;
    const note = noteFor(previous.id, task.id);
    if (note) {
      links.push({
        id: `context:${previous.id}:${task.id}`,
        source: previous.id,
        target: task.id,
        kind: "context",
        label: note.label,
        stated: true,
        evidence: `${note.label} · ${zh ? "按时间排列，不表示执行依赖。" : "Arranged in time; no execution dependency is implied."}`,
      });
      join(previous.id, task.id);
      continue;
    }
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
