import { ACTIVE, type MapEvent, type MapTask, type MapGraph } from "./model";
import { layoutGraph } from "./graphLayout";
import { roleLabel } from "../lib/enumLabels";
import { translate } from "../i18n";
import { relativizeWorkspacePaths } from '../lib/workspacePaths';

export const MAP_FRAME = { width: 1440, height: 1080 };
export const MAX_STEPS_PER_CARD = 12;
export interface MapCard {
  id: string;
  task: MapTask;
  /** Stable task order for arrival animations and replay. */
  ordinal: number;
  part: number;
  partCount: number;
  start: number;
  end: number;
  totalSteps: number;
  previousId?: string;
  nextId?: string;
}
export type StepKind = "plan" | "execution" | "review" | "result";
export interface SubmapStep {
  id: string;
  kind: StepKind;
  title: string;
  summary?: string;
  detail: string;
  status: string;
  ts?: number;
  source: "task" | "event";
  eventIds: string[];
}
export const STEP_KINDS: StepKind[] = ["plan", "execution", "review", "result"];
export const noDetails = (zh: boolean) =>
  zh ? "这一步还没有留下记录" : "Nothing has been written down for this step yet.";

function clipSentence(value: string, limit = 140): string {
  const text = value.replace(/\s+/g, " ").trim();
  const match = text.match(/^.*?[。！？!?.](?=\s|$)/);
  const sentence = (match ? match[0] : text).trim();
  return sentence.length > limit ? `${sentence.slice(0, limit - 1).trimEnd()}…` : sentence;
}
function outcomeSentence(status: string, zh: boolean): string {
  switch (status) {
    case "done": return zh ? "这项任务已经完成。" : "This task was completed.";
    case "failed": return zh ? "这项任务没有达到目标。" : "This task did not reach its goal.";
    case "cancelled": return zh ? "这项任务被取消了。" : "This task was cancelled.";
    default: return "";
  }
}

/** Read only the letters the server emits, in order, including questions and answers. */
export function buildSubmap(task: MapTask, events: MapEvent[], zh: boolean, workspace?: string): SubmapStep[] {
  const turn = task.turn_kind === 'qa';
  const rows: SubmapStep[] = [{ id: `${task.id}:brief`, kind: 'plan',
    title: turn ? (zh ? '你提出的问题' : 'What you asked') : (zh ? '任务目标' : 'Task goal'),
    detail: relativizeWorkspacePaths(task.title, workspace), status: 'recorded', source: 'task', eventIds: [] }];
  const seen = new Set<string>();
  const name = (role?: string) => roleLabel(role, key => translate(key, {}, zh ? 'zh-CN' : 'en'));
  for (const event of events.filter(e => e.item_id === task.id).sort((a, b) => a.ts - b.ts)) {
    if (seen.has(event.id)) continue;
    seen.add(event.id);
    const text = relativizeWorkspacePaths(event.text, workspace);
    const reply = event.type === 'turn.replied';
    rows.push({ id: event.id,
      kind: reply ? 'result' : event.role === 'reviewer' ? 'review' : 'execution',
      title: reply ? (zh ? 'Argus 的回答' : 'What Argus answered')
        : event.type === 'turn.asked' ? (zh ? '你补充的说明' : 'Your clarification')
        : event.role === event.to ? (zh ? `${name(event.role)}接着做` : `${name(event.role)} continues`)
        : `${name(event.role)} → ${name(event.to)}`,
      summary: clipSentence(text) || undefined, detail: text || noDetails(zh),
      status: reply ? 'done' : 'recorded', ts: event.ts, source: 'event', eventIds: [event.id] });
  }
  if (turn && !rows.some(row => row.kind === 'result')) rows.push({ id: `${task.id}:answer`, kind: 'result',
    title: zh ? '请求状态' : 'Request status',
    detail: task.status === 'cancelled' ? (zh ? '本次回答已中断。' : 'This answer was interrupted.') : (zh ? '尚无回答。' : 'No answer recorded.'),
    status: task.status, source: 'task', eventIds: [] });
  if (!turn && !rows.some(row => row.kind === 'execution') && ACTIVE.has(task.status)) rows.push({
    id: `${task.id}:active`, kind: 'execution', title: zh ? '正在进行的工作' : 'Work under way',
    detail: task.latest_action?.text || '', status: task.status, source: 'task', eventIds: [] });
  if (!turn && ['done', 'failed', 'cancelled'].includes(task.status)) rows.push({
    id: `${task.id}:outcome`, kind: 'result', title: zh ? '任务的最终状态' : 'Where this task ended up',
    detail: task.latest_action?.text || outcomeSentence(task.status, zh), status: task.status, source: 'task', eventIds: [] });
  return rows;
}

export interface SubmapLink {
  id: string;
  source: string;
  target: string;
  relation:
    | "assignment"
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
  /** The steps are stacked in one column for a narrow canvas. */
  stacked?: boolean;
}

/** On a narrow canvas every card reads top to bottom in one column: at a
 * readable scale that column is as wide as a phone screen, so nothing is cut
 * off at the right edge and the reader pans down, never sideways. (A wide
 * three-column layout used to keep its right two columns outside the
 * viewport on a phone.) */
const STACKED_WIDTH = 480;

/** Content bounds are computed before zooming, so disclosure cannot move ports. */
export function layoutSubmap(
  task: MapTask,
  events: MapEvent[],
  zh: boolean,
  steps = buildSubmap(task, events, zh),
  offset = 0,
  narrow = false,
): SubmapLayout {
  // Read downward within a column, then advance right. Equal spacing makes
  // phase changes legible without leaving empty cells between sparse groups.
  const pitchX = 340,
    pitchY = 236;
  const stacked = narrow && steps.length > 0;
  const minWidth = stacked ? STACKED_WIDTH : 640;
  let rows = stacked ? steps.length : 1,
    best = Infinity;
  for (let n = 1; !stacked && n <= Math.min(3, steps.length); n++) {
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
  const width = Math.max(minWidth, count * 232 + (count - 1) * 108 + 96);
  // A stacked card's heading wraps onto three rows, so its steps start lower.
  const top = stacked ? 320 : 180;
  const height = top + 228 + (rows - 1) * pitchY;
  const left = (width - (count * 232 + (count - 1) * 108)) / 2;
  const positions: SubmapLayout["positions"] = {};
  const columns = Array.from({ length: count }, (_, col) => {
    const start = col * rows,
      end = Math.min(start + rows, steps.length),
      x = left + col * pitchX;
    const slice = steps.slice(start, end);
    slice.forEach((step, row) => {
      positions[step.id] = { x, y: top + row * pitchY };
    });
    const from = offset + start + 1, to = offset + end;
    const fallback = from === to
      ? zh ? `环节 ${from}` : `Step ${from}`
      : zh ? `环节 ${from}–${to}` : `Steps ${from}–${to}`;
    const title = col === 0 && offset === 0 ? (zh ? '起点' : 'Setting out') : fallback;
    return { id: `steps:${offset + start}`, title, x, y: top - 38 };
  });
  return {
    steps,
    links: submapLinks(steps, zh),
    columns,
    positions,
    width,
    height,
    ...(stacked ? { stacked } : {}),
  };
}

export function frameForSubmap(layout: Pick<SubmapLayout, "width" | "height" | "stacked">) {
  const frame = layout.stacked ? { width: 900, height: 875 } : MAP_FRAME;
  return { ...frame, scale: Math.min(frame.width / layout.width, frame.height / layout.height) };
}

/** A relationship label states what the record actually supports. */
export function submapLinks(steps: SubmapStep[], zh: boolean): SubmapLink[] {
  return steps.slice(1).map((target, i) => {
    const source = steps[i];
    let relation: SubmapLink["relation"] = "record_order";
    let label = zh ? "接着" : "then";
    let explanation = zh
      ? "同一任务的下一条记录。"
      : "The task's next record.";
    if (source.kind === "plan" && ["plan", "execution"].includes(target.kind)) {
      relation = "assignment";
      label = zh
        ? target.kind === "plan"
          ? "纳入计划"
          : "执行此任务"
        : target.kind === "plan"
          ? "planned"
          : "carried out";
      explanation = zh
        ? "同一任务的目标／计划与其执行记录关联。"
        : "The task brief or plan is linked to execution of that same task.";
    } else if (target.kind === "result" && target.source === "task") {
      relation = "snapshot";
      label = zh ? "最终状态" : "ended as";
      explanation = zh
        ? "任务的最终状态。"
        : "The task's final state.";
    } else if (
      target.kind === "result" &&
      ["review", "execution"].includes(source.kind)
    ) {
      relation = "outcome";
      label = zh ? "形成结果" : "led to";
      explanation = zh
        ? "这一轮工作的结果。"
        : "The outcome of this work.";
    }
    const contextual = ["record_order", "snapshot"].includes(relation);
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

function stepPages(steps: SubmapStep[]): SubmapStep[][] {
  const pages: SubmapStep[][] = [];
  for (let index = 0; index < steps.length; index += MAX_STEPS_PER_CARD)
    pages.push(steps.slice(index, index + MAX_STEPS_PER_CARD));
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
  links = graph.links,
  selectedParts?: ReadonlyMap<string, number>,
  narrow = false,
  workspace?: string,
) {
  const cards: MapCard[] = [];
  const layouts: Record<string, SubmapLayout> = {};
  for (const [ordinal, task] of graph.tasks.entries()) {
    const steps = buildSubmap(task, events, zh, workspace);
    const pages = stepPages(steps);
    const count = pages.length;
    // A part's ordinal is stable when subsequent events arrive. Original task
    // and step IDs remain the source of truth for references and model copy.
    const idFor = (part: number) => JSON.stringify(["part", task.id, part]);
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
      layouts[id] = layoutSubmap(task, events, zh, slice, start, narrow);
      start += slice.length;
    }
  }
  // Pages stay inside the task. The overview keeps one fixed card per task.
  const allCards = cards.slice();
  cards.length = 0;
  for (const task of graph.tasks) {
    const pages = allCards.filter(card => card.task.id === task.id);
    const card = pages.find(card => card.part === selectedParts?.get(task.id)) ?? pages.at(-1)!;
    const layout = layouts[card.id];
    cards.push({ ...card, id: task.id });
    layouts[task.id] = layout;
  }
  const frames = Object.fromEntries(cards.map(({ id }) => [id, frameForSubmap(layouts[id])]));
  const displayLinks = links;
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
  return { cards, allCards, links: displayLinks, layouts, positions, frames, structure };
}
