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
  summary?: string;
  detail: string;
  status: string;
  ts?: number;
  round?: number;
  episode?: number;
  source: "task" | "event" | "interval" | "team";
  eventIds: string[];
  teamId?: string;
  teamTaskId?: string;
  teamRole?: string;
  deps?: string[];
  updatedAt?: number;
  revision?: string;
}
export const STEP_KINDS: StepKind[] = [
  "plan",
  "execution",
  "review",
  "revision",
  "result",
];

function teamTitle(event: MapEvent, zh: boolean): string {
  const route = event.team_task_id?.match(/route-(\d+)/)?.[1];
  const label = event.team_role === 'idea-route'
    ? zh ? '研究路线' : 'Research route'
    : event.team_role === 'idea-review'
      ? zh ? '独立复核' : 'Independent review'
      : event.team_role === 'idea-selector'
        ? zh ? '方案选择' : 'Idea selection'
        : '';
  return label ? `${label}${route ? ` ${route}` : ''}` : event.title || (zh ? '并行子任务' : 'Parallel task');
}

function teamSummary(event: MapEvent, zh: boolean, waitingForDeps: boolean): string {
  if (event.pending_question) return zh ? '需要答复，展开查看具体问题' : 'Needs your input; open to read the question';
  if (event.status === 'failed') return zh ? '本次执行失败，展开查看原因' : 'This attempt failed; open to read the reason';
  if (event.status === 'blocked') return zh ? '执行受阻，展开查看原因' : 'Work is blocked; open to read the reason';
  if (event.status === 'done') return event.team_role === 'idea-review'
    ? zh ? '独立复核已完成，展开查看记录' : 'Independent review completed; open to read the record'
    : zh ? '子任务执行已完成，展开查看记录' : 'Subtask execution completed; open to read the record';
  if (event.status === 'pending') return waitingForDeps
    ? zh ? '等待前置子任务完成后开始' : 'Waiting for prerequisite subtasks to finish'
    : zh ? '等待分配 Agent 执行' : 'Waiting for an agent to start';
  if (ACTIVE.has(event.status || '')) return event.team_role === 'idea-route'
    ? zh ? '正在开展来源研究，整理候选方案' : 'Researching sources and developing a candidate idea'
    : event.team_role === 'idea-review'
      ? zh ? '正在独立核对依据、创新性和风险' : 'Independently checking evidence, novelty and risks'
      : event.team_role === 'idea-selector'
        ? zh ? '正在对比研究路线及复核意见' : 'Comparing research routes and independent reviews'
        : zh ? 'Agent 正在执行此子任务' : 'An agent is working on this subtask';
  return zh ? '展开查看子任务执行记录' : 'Open to read the subtask record';
}

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
    .sort((a, b) => a.ts - b.ts || (a.type === 'team.task' && b.type === 'team.task'
      ? (a.team_task_id || a.id).localeCompare(b.team_task_id || b.id) : 0));
  const teamEvents = new Map(sorted.filter((e) => e.type === 'team.task').map((e) => [e.id, e]));
  for (const e of sorted) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    if (e.type === 'team.task') {
      const deps = [...new Set(e.deps || [])];
      const dependencyTitles = deps.map((id) => teamEvents.has(id)
        ? teamTitle(teamEvents.get(id)!, zh) : (zh ? '其他记录中的子任务' : 'Task outside this view'));
      const description = readableRecord(e.text);
      rows.push({
        id: e.id,
        kind: e.team_role === 'idea-review' || e.role === 'reviewer' ? 'review'
          : e.team_role === 'idea-selector' ? 'plan' : 'execution',
        title: teamTitle(e, zh),
        summary: teamSummary(e, zh, deps.some((id) => teamEvents.has(id) && teamEvents.get(id)!.status !== 'done')),
        detail: [
          description,
          e.reason && !description.includes(e.reason) ? e.reason : '',
          e.pending_question ? `${zh ? '需要答复' : 'Needs input'}: ${e.pending_question}` : '',
          dependencyTitles.length ? `${zh ? '依赖' : 'Depends on'}: ${dependencyTitles.join(' · ')}` : '',
        ].filter(Boolean).join('\n\n'),
        status: e.pending_question ? 'question' : e.status || 'unknown',
        ts: e.ts,
        source: 'team',
        eventIds: [e.id],
        teamId: e.team_id,
        teamTaskId: e.team_task_id,
        teamRole: e.team_role,
        deps,
        updatedAt: e.updated_ts,
        revision: e.revision,
      });
      continue;
    }
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
    | "snapshot"
    | "dependency";
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
  const team = steps.filter((step) => step.source === 'team');
  if (team.length) {
    const ordinary = steps.filter((step) => step.source !== 'team');
    const byId = new Map(team.map((step) => [step.id, step]));
    const brief = ordinary.find((step) => step.source === 'task' && step.kind === 'plan');
    const branches: SubmapLink[] = [];
    for (const target of team) {
      for (const dependency of target.deps || []) {
        const source = byId.get(dependency);
        if (!source || source.id === target.id || source.teamId !== target.teamId) continue;
        branches.push({
          id: `link:${source.id}:${target.id}`,
          source: source.id,
          target: target.id,
          relation: 'dependency',
          label: zh ? '前置任务' : 'Depends on',
          explanation: zh ? `${target.title} 的任务记录明确依赖 ${source.title}。`
            : `${target.title} explicitly depends on ${source.title} in its taskboard.`,
          contextual: false,
        });
      }
      if (brief && !target.deps?.length) branches.push({
        id: `link:${brief.id}:${target.id}`,
        source: brief.id,
        target: target.id,
        relation: 'assignment',
        label: zh ? '任务分支' : 'Branch',
        explanation: zh ? '该子任务属于当前主任务；此线不表示等待主任务完成。'
          : 'This worker belongs to the current mission; the link does not require the parent to finish first.',
        contextual: true,
      });
    }
    // Parallel workers have only their recorded dependencies. Do not connect
    // neighboring workers into an invented serial execution/review chain.
    return [...submapLinks(ordinary, zh), ...branches];
  }
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

/** Keep adjacent route/review dependencies together when a card has room. */
function stepPages(steps: SubmapStep[]): SubmapStep[][] {
  const pages: SubmapStep[][] = [];
  let page: SubmapStep[] = [];
  for (let index = 0; index < steps.length;) {
    const group = [steps[index++]];
    while (index < steps.length && group.length < MAX_STEPS_PER_CARD) {
      const previous = group.at(-1)!;
      const next = steps[index];
      if (previous.source !== 'team' || next.source !== 'team'
        || previous.teamId !== next.teamId || !next.deps?.includes(previous.id)) break;
      group.push(next);
      index++;
    }
    if (page.length && page.length + group.length > MAX_STEPS_PER_CARD) {
      pages.push(page);
      page = [];
    }
    page.push(...group);
  }
  if (page.length) pages.push(page);
  return pages;
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
    const pages = stepPages(steps);
    const count = pages.length;
    // A part's ordinal is stable when subsequent events arrive. Original task
    // and step IDs remain the source of truth for references and model copy.
    const idFor = (part: number) =>
      part === 1 ? task.id : JSON.stringify(["part", task.id, part]);
    let start = 0;
    for (let part = 1; part <= count; part++) {
      const slice = pages[part - 1];
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
          label: boundary ? zh ? "继续" : "Continued" : zh ? '更多分支' : 'More branches',
          evidence: boundary
            ? `${task.title} · ${boundary.label} · ${steps[start - 1].title} → ${slice[0].title}`
            : `${task.title} · ${zh ? '同一任务的其他分支，不表示串行依赖。' : 'Other branches of the same mission, without a serial dependency.'}`,
        });
      }
      start += slice.length;
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
