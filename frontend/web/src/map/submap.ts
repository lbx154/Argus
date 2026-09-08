import {
  ACTIVE,
  connectMap,
  type MapEvent,
  type MapTask,
  type MapGraph,
  type MapLink,
} from "./model";
import { layoutGraph } from "./graphLayout";

export const MAP_FRAME = { width: 1440, height: 1080 };
export const MAX_STEPS_PER_CARD = 12;

export interface MapCard {
  id: string;
  task: MapTask;
  ordinal: number;
  part: number;
  partCount: number;
  start: number;
  end: number;
  totalSteps: number;
  previousId?: string;
  nextId?: string;
}

export type StepKind = "plan" | "execution" | "review" | "revision" | "result";
export interface SubmapStep {
  id: string;
  kind: StepKind;
  title: string;
  detail: string;
  status: string;
  ts?: number;
  round?: number;
  episode?: number;
  source: "task" | "event" | "interval";
  eventIds: string[];
}
export const STEP_KINDS: StepKind[] = [
  "plan",
  "execution",
  "review",
  "revision",
  "result",
];

/** Keep scientific prose visible while hiding the runner's control footer. */
export function readableRecord(value: string | undefined | null): string {
  return String(value || "")
    .split(/\r?\n/)
    .filter(
      (line) =>
        !/^(?:Decision\s*:|(?:MILESTONE_STATUS|NEXT_OWNER|OPERATOR_QUESTION|OPERATOR_OPTIONS)\s*=)/i.test(
          line.trim(),
        ),
    )
    .map((line) =>
      line
        .replace(/^\s*(?:RESULT|SUMMARY)\s*=\s*/i, "")
        .replace(/^\s*NEXT_ACTION\s*=\s*/i, ""),
    )
    .join("\n")
    .trim();
}

/** Display observations, including their evidence strength; never fill a missing stage with success. */
export function buildSubmap(
  task: MapTask,
  events: MapEvent[],
  zh: boolean,
): SubmapStep[] {
  const rows: SubmapStep[] = [
    {
      id: `${task.id}:brief`,
      kind: "plan",
      title: zh ? "任务目标" : "Task brief",
      detail: task.objective || task.title,
      status: "recorded",
      source: "task",
      eventIds: [],
    },
  ];
  const seen = new Set<string>();
  let episode = 0;
  const sorted = events
    .filter((e) => e.item_id === task.id)
    .sort((a, b) => a.ts - b.ts);
  for (const e of sorted) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    if (e.type === "life.mission.started") episode++;
    const kind: StepKind | null =
      e.type.includes("review") ||
      (e.type === "life.phase.started" && e.role === "reviewer")
        ? "review"
        : e.type === "life.planner.task_added"
          ? "plan"
          : e.type === "life.mission.completed" ||
              e.type === "life.mission.failed"
            ? "result"
            : e.type === "round.start" ||
                e.type === "round.main.completed" ||
                e.type === "life.mission.started" ||
                e.type === "life.phase.started"
              ? "execution"
              : null;
    if (!kind) continue;
    const round = e.round_index;
    const finished =
      e.type.endsWith(".completed") || e.type.endsWith(".failed");
    const reviewSkipped = kind === "review" && e.review_skipped === true;
    const status =
      (reviewSkipped ? "skipped" : e.status) ||
      (e.success === false || e.type.endsWith(".failed")
        ? "failed"
        : e.success === true
          ? "done"
          : finished
            ? "recorded"
            : "started");
    const title = reviewSkipped
      ? zh ? "审查未执行" : "Review not performed"
      : kind === "review"
        ? finished
          ? zh
            ? "审查意见"
            : "Review outcome"
          : zh
            ? "开始审查"
            : "Review started"
        : kind === "result"
          ? zh
            ? "执行结果"
            : "Execution result"
          : kind === "plan"
            ? zh
              ? "任务进入计划"
              : "Added to plan"
            : e.type === "life.mission.started"
              ? zh
                ? "开始执行"
                : "Execution started"
              : e.type === "round.main.completed"
                ? zh
                  ? "本轮执行记录"
                  : "Round execution"
                : zh
                  ? "执行尝试"
                  : "Execution attempt";
    rows.push({
      id: e.id,
      kind,
      title,
      detail: [
        readableRecord(e.text) ||
          (zh ? "暂无详细记录" : "Details are not available yet."),
        reviewSkipped && e.next_action
          ? `${zh ? "下一步" : "Next action"}: ${e.next_action}`
          : "",
      ].filter(Boolean).join("\n\n"),
      status,
      ts: e.ts,
      round,
      episode,
      source: e.association === "single_active_window" ? "interval" : "event",
      eventIds: [e.id],
    });
    if (
      !reviewSkipped && e.next_action &&
      ["continue", "blocked", "replan", "replan_requested"].includes(
        e.status || "",
      )
    ) {
      rows.push({
        id: `${e.id}:next`,
        kind: "revision",
        title: zh ? "建议的修订" : "Requested revision",
        detail: e.next_action,
        status: "requested",
        ts: e.ts,
        round,
        episode,
        source: e.association === "single_active_window" ? "interval" : "event",
        eventIds: [e.id],
      });
    }
  }
  if (!rows.some((r) => r.kind === "execution") && ACTIVE.has(task.status))
    rows.push({
      id: `${task.id}:active`,
      kind: "execution",
      title: zh ? "执行进展" : "Execution progress",
      detail: task.summary || "",
      status: task.status,
      source: "task",
      eventIds: [],
    });
  if (
    !rows.some((r) => r.kind === "result") &&
    ["done", "failed", "aborted", "skipped", "superseded"].includes(task.status)
  )
    rows.push({
      id: `${task.id}:outcome`,
      kind: "result",
      title: zh ? "任务状态记录" : "Recorded task outcome",
      detail: task.summary || "",
      status: task.status,
      source: "task",
      eventIds: [],
    });
  // Collapse start/progress/completion into one readable node only within an
  // explicitly numbered round and the same observed mission episode.
  const merged: SubmapStep[] = [];
  const groups = new Map<string, SubmapStep>();
  for (const row of rows) {
    const mergeable =
      row.round != null && ["execution", "review"].includes(row.kind);
    const key = `${row.episode}:${row.round}:${row.kind}`;
    const previous = mergeable ? groups.get(key) : undefined;
    if (previous) {
      if (row.kind === "review") previous.title = row.status === "skipped"
        ? row.title : zh ? "审查与反馈" : "Review & feedback";
      previous.detail = row.detail;
      previous.status = row.status;
      previous.eventIds.push(...row.eventIds);
      if (row.source === "interval") previous.source = "interval";
    } else {
      const copy = {
        ...row,
        title: mergeable && row.status !== "skipped"
          ? row.kind === "review"
            ? zh
              ? "审查与反馈"
              : "Review & feedback"
            : zh
              ? "执行尝试"
              : "Execution attempt"
          : row.title,
        eventIds: [...row.eventIds],
      };
      merged.push(copy);
      if (mergeable) groups.set(key, copy);
    }
  }
  return merged;
}

export interface SubmapLink {
  id: string;
  source: string;
  target: string;
  relation:
    | "assignment"
    | "review"
    | "revision"
    | "next_attempt"
    | "outcome"
    | "record_order"
    | "snapshot";
  label: string;
  explanation: string;
  contextual: boolean;
}

export interface SubmapLayout {
  steps: SubmapStep[];
  links: SubmapLink[];
  columns: Array<{
    id: string;
    title: string;
    x: number;
    y: number;
  }>;
  positions: Record<string, { x: number; y: number }>;
  width: number;
  height: number;
}

/** Content bounds are computed before zooming, so disclosure cannot move ports. */
export function layoutSubmap(
  task: MapTask,
  events: MapEvent[],
  zh: boolean,
  steps = buildSubmap(task, events, zh),
  offset = 0,
): SubmapLayout {
  // Read downward within a column, then advance right. Equal spacing makes
  // phase changes legible without leaving empty cells between sparse groups.
  const pitchX = 340,
    pitchY = 236;
  let rows = 1,
    best = Infinity;
  for (let n = 1; n <= Math.min(3, steps.length); n++) {
    const columns = Math.ceil(steps.length / n);
    const width = Math.max(640, columns * 232 + (columns - 1) * 108 + 96);
    const height = 408 + (n - 1) * pitchY;
    const score =
      Math.abs(Math.log(width / height / 1.6)) +
      (0.6 * (columns * n - steps.length)) / (columns * n);
    if (score < best) {
      best = score;
      rows = n;
    }
  }
  const count = Math.ceil(steps.length / rows);
  const width = Math.max(640, count * 232 + (count - 1) * 108 + 96);
  const height = 408 + (rows - 1) * pitchY;
  const left = (width - (count * 232 + (count - 1) * 108)) / 2;
  const positions: SubmapLayout["positions"] = {};
  const columns = Array.from({ length: count }, (_, col) => {
    const start = col * rows,
      end = Math.min(start + rows, steps.length),
      x = left + col * pitchX;
    steps.slice(start, end).forEach((step, row) => {
      positions[step.id] = { x, y: 180 + row * pitchY };
    });
    return {
      id: `steps:${offset + start}`,
      title: zh
        ? `环节 ${offset + start + 1}–${offset + end}`
        : `Steps ${offset + start + 1}–${offset + end}`,
      x,
      y: 142,
    };
  });
  return {
    steps,
    links: submapLinks(steps, zh),
    columns,
    positions,
    width,
    height,
  };
}

export function frameForSubmap(layout: Pick<SubmapLayout, "width" | "height">) {
  const scale = Math.max(
    600 / layout.height,
    Math.min(1000 / layout.height, MAP_FRAME.width / layout.width),
  );
  return { width: layout.width * scale, height: layout.height * scale, scale };
}

/** A relationship label states what the record actually supports. */
export function submapLinks(steps: SubmapStep[], zh: boolean): SubmapLink[] {
  return steps.slice(1).map((target, i) => {
    const source = steps[i];
    const sameEpisode = source.episode === target.episode;
    const sameRound =
      sameEpisode && source.round != null && source.round === target.round;
    let relation: SubmapLink["relation"] = "record_order";
    let label = zh ? "后续记录" : "Later record";
    let explanation = zh
      ? "同一任务的相邻观察，未确认直接因果或执行依赖。"
      : "Adjacent observations of the same task; no causal dependency is asserted.";
    if (source.kind === "plan" && ["plan", "execution"].includes(target.kind)) {
      relation = "assignment";
      label = zh
        ? target.kind === "plan"
          ? "纳入计划"
          : "执行此任务"
        : "Execute";
      explanation = zh
        ? "同一任务的目标／计划与其执行记录关联。"
        : "The task brief or plan is linked to execution of that same task.";
    } else if (
      source.kind === "execution" &&
      target.kind === "review" &&
      sameRound
    ) {
      relation = "review";
      label = zh ? "提交审查" : "Review";
      explanation = zh
        ? "同一个任务、同一执行段、同一轮次的执行与审查记录。"
        : "Execution and review belong to the same task, episode and numbered round.";
    } else if (
      source.kind === "review" &&
      target.kind === "revision" &&
      target.eventIds.some((id) => source.eventIds.includes(id))
    ) {
      relation = "revision";
      label = zh ? "提出修订" : "Revise";
      explanation = zh
        ? "这条修订建议来自对应的审查记录。"
        : "This revision was requested in the corresponding review.";
    } else if (
      source.kind === "revision" &&
      target.kind === "execution" &&
      sameEpisode &&
      source.round != null &&
      target.round != null &&
      target.round > source.round
    ) {
      relation = "next_attempt";
      label = zh ? "进入下轮" : "Next round";
      explanation = zh
        ? "修订建议之后出现了同一任务的下一轮执行；不表示建议的全部内容已被采纳。"
        : "A later round follows the revision request; this does not certify every requested change was applied.";
    } else if (target.kind === "result" && target.source === "task") {
      relation = "snapshot";
      label = zh ? "状态记录" : "Recorded status";
      explanation = zh
        ? "任务状态记录；部分执行过程可能缺失。"
        : "Links to the captured state of this task; intermediate records may be missing.";
    } else if (
      target.kind === "result" &&
      ["review", "execution", "revision"].includes(source.kind)
    ) {
      relation = "outcome";
      label = zh ? "形成结果" : "Outcome";
      explanation = zh
        ? "同一任务的后续完成／失败事件，不等同于成功认证。"
        : "A completion or failure event of this task, not a certification of success.";
    }
    const contextual =
      ["record_order", "snapshot"].includes(relation) ||
      source.source === "interval" ||
      target.source === "interval";
    if (source.source === "interval" || target.source === "interval")
      explanation += zh
        ? " 部分旧记录按唯一活动任务区间归属，因此使用虚线。"
        : " Some legacy observations are associated by the sole active mission window, so this link is dashed.";
    return {
      id: `link:${source.id}:${target.id}`,
      source: source.id,
      target: target.id,
      relation,
      label,
      explanation,
      contextual,
    };
  });
}
export interface FocusNode {
  id: string;
  position: { x: number; y: number };
  hidden?: boolean;
  width?: number;
  height?: number;
}

/** Pure geometry. Empty-space zoom never opens a distant task. */
export function zoomTarget(
  nodes: FocusNode[],
  viewport: { x: number; y: number; zoom: number },
  point: { x: number; y: number },
  center: { x: number; y: number },
): string | null {
  for (const p of [point, center]) {
    const world = {
      x: (p.x - viewport.x) / viewport.zoom,
      y: (p.y - viewport.y) / viewport.zoom,
    };
    const found = nodes.find(
      (n) =>
        !n.hidden &&
        world.x >= n.position.x &&
        world.x <= n.position.x + (n.width ?? 1152) &&
        world.y >= n.position.y &&
        world.y <= n.position.y + (n.height ?? 824),
    );
    if (found) return found.id;
  }
  return null;
}

/** Size nested content before placing the outer graph. */
export function layoutScene(
  graph: MapGraph,
  events: MapEvent[],
  zh: boolean,
  previous?: {
    structure: string;
    positions: Record<string, { x: number; y: number }>;
  },
  links = connectMap(graph, [], zh),
) {
  const cards: MapCard[] = [];
  const layouts: Record<string, SubmapLayout> = {};
  const continuations: MapLink[] = [];
  const lastCard = new Map<string, string>();
  for (const [ordinal, task] of graph.tasks.entries()) {
    const steps = buildSubmap(task, events, zh);
    const count = Math.ceil(steps.length / MAX_STEPS_PER_CARD);
    // A part's ordinal is stable when subsequent events arrive. Original task
    // and step IDs remain the source of truth for references and model copy.
    const idFor = (part: number) =>
      part === 1 ? task.id : JSON.stringify(["part", task.id, part]);
    for (let part = 1; part <= count; part++) {
      const start = (part - 1) * MAX_STEPS_PER_CARD;
      const slice = steps.slice(start, start + MAX_STEPS_PER_CARD);
      const id = idFor(part);
      cards.push({
        id,
        task,
        ordinal: ordinal + 1,
        part,
        partCount: count,
        start: start + 1,
        end: start + slice.length,
        totalSteps: steps.length,
        previousId: part > 1 ? idFor(part - 1) : undefined,
        nextId: part < count ? idFor(part + 1) : undefined,
      });
      layouts[id] = layoutSubmap(task, events, zh, slice, start);
      if (part > 1) {
        const boundary = submapLinks([steps[start - 1], slice[0]], zh)[0];
        continuations.push({
          id: JSON.stringify(["continuation", task.id, part]),
          source: idFor(part - 1),
          target: id,
          kind: "continuation",
          label: zh ? "继续" : "Continued",
          evidence: `${task.title} · ${boundary.label} · ${steps[start - 1].title} → ${slice[0].title}`,
        });
      }
    }
    lastCard.set(task.id, idFor(count));
  }
  const frames = Object.fromEntries(
    Object.entries(layouts).map(([id, layout]) => [id, frameForSubmap(layout)]),
  );
  const displayLinks = [
    ...links.map((e) => ({
      ...e,
      source: lastCard.get(e.source)!,
      target: e.target,
    })),
    ...continuations,
  ];
  const ids = cards.map((card) => card.id);
  const structure = JSON.stringify([
    ids.map((id) => [id, frames[id].width, frames[id].height]),
    displayLinks.map((e) => [e.source, e.target, e.kind]),
  ]);
  // Streaming prose/status changes do not require another geometry search.
  const positions =
    previous?.structure === structure
      ? previous.positions
      : layoutGraph(ids, displayLinks, frames);
  return { cards, links: displayLinks, layouts, positions, frames, structure };
}
