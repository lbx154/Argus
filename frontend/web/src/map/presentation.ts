import type { Dataset, MapEvent, MapTask } from "./model";
import { ACTIVE, isStageHold, latestCertifiedTask, statusKey } from "./model";
import type { SubmapStep } from "./submap";
import { humanizeHarnessNote, readableRecord } from "./submap";

/** Why a task waits on the reader: its open question, or the reason its last
 * attempt failed, said the way the cards say it. A record that is only a
 * technical receipt becomes the colleague's sentence for it; a record that
 * says nothing is reported as exactly that. */
export function attentionReason(task: MapTask, events: MapEvent[], zh: boolean): string {
  if (task.pending_question) return task.pending_question;
  const failure = events.filter((event) => event.item_id === task.id &&
    (event.status === "failed" || event.success === false || event.type.endsWith(".failed")))
    .sort((a, b) => b.ts - a.ts)[0];
  // A stage hold or a settled failure often records its reason only on the
  // backlog row; the Manager's own sentence follows the harness prefix.
  const recorded = (task.last_error || "").replace(/^manager stage (?:hold|rollback):\s*/i, "");
  const raw = failure?.reason || failure?.text || recorded;
  const note = humanizeHarnessNote(raw, zh);
  const cut = note.receipt ? raw.lastIndexOf(note.receipt) : -1;
  const prose = readableRecord(cut >= 0 ? raw.slice(0, cut) : raw);
  return note.summary || prose ||
    (zh ? "这项任务没有完成，记录里没有写明原因。" : "This task did not finish, and the record does not say why.");
}

/** The recorded words, stripped of the harness prefix; shown as detail. */
function recordedDetail(task: MapTask): string {
  return (task.last_error || "").replace(/^manager stage (?:hold|rollback):\s*/i, "").trim();
}

/** What waits on the reader, as a short reason and next step in the reader's
 * language, with the agent's own (possibly other-language) words kept apart
 * as detail rather than shown as the headline.
 *
 * ``written`` is the plain-language account of this task's outcome that the
 * writer already produced in the reader's language, when one is cached. A
 * stage hold's reason is the Manager's free-form judgement, so the only
 * faithful reader-language reason is a written one; without it the Manager's
 * own words are the reason and are shown, not hidden (``showDetail``). */
export function attentionSummary(task: MapTask, events: MapEvent[], zh: boolean, written = ""):
  { reason: string; next: string; detail: string; showDetail: boolean } {
  if (task.pending_question) return {
    reason: task.pending_question,
    next: zh ? "下一步：回答这个问题，团队才会继续。" : "Next: answer this question so the team can continue.",
    detail: "",
    showDetail: false,
  };
  if (isStageHold(task)) {
    const detail = recordedDetail(task);
    const said = written.trim();
    return {
    reason: said
        ? zh ? `工作已暂停：${said}` : `Work paused: ${said}`
        : detail
          ? zh ? "工作已暂停：当前结果还没有满足要求，具体原因见下方说明。"
            : "Work paused: the current result does not yet meet the requirements. The reason is shown below."
          : zh ? "工作已暂停：当前结果还没有满足要求，记录里没有说明具体原因。"
            : "Work paused: the current result does not yet meet the requirements. No specific reason was recorded.",
      // A recorded hold ends the bounded run: nothing is scheduled after it
      // and nothing resumes on its own, so the next move is the reader's.
      next: zh
        ? "工作不会自动继续。请看暂停原因，再在对话里告诉我接下来做什么。"
        : "Work will not resume on its own. Read the reason, then tell me what to do next in the conversation.",
      detail,
      showDetail: Boolean(detail) && !said,
    };
  }
  const reason = attentionReason(task, events, zh);
  const detail = recordedDetail(task);
  return {
    reason: zh ? `执行失败：${reason}` : `Execution failed: ${reason}`,
    next: zh ? "下一步：查看原因后重试，或在对话里调整任务。" : "Next: check the reason, then retry or adjust the task in the conversation.",
    detail: detail && !reason.includes(detail) ? detail : "",
    showDetail: false,
  };
}

const clip = (text: string, size = 80) => text.length > size ? `${text.slice(0, size)}…` : text;

/** Three lines an outsider can read: the current goal, what has been
 * concluded (only an acceptance still standing counts), and what comes next. */
export function projectBrief(tasks: MapTask[], events: MapEvent[], zh: boolean, written: (task: MapTask) => string = () => ""):
  { goal: string; current: string; conclusion: string; next: string } {
  const work = tasks.filter((task) => task.turn_kind !== "qa" && task.kind !== "turn");
  const byTime = [...work].sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0));
  const latest = byTime[0];
  const goal = latest ? clip((latest.objective || latest.title).trim()) : (zh ? "还没有任务" : "No task yet");
  const accepted = latestCertifiedTask(tasks, events);
  const label = (task: MapTask) => {
    const key = statusKey(task);
    return ({
      held: zh ? "工作已暂停" : "work paused",
      failed: zh ? "这项工作没有做完" : "this task did not finish",
      review_unavailable: zh ? "结果检查没有完成" : "the result check did not finish",
      done: zh ? "已完成" : "done",
      running: zh ? "进行中" : "running",
      pending: zh ? "待开始" : "planned",
      question: zh ? "等你回答" : "waiting on you",
      paused: zh ? "已暂停" : "paused",
    } as Record<string, string>)[key] || (zh ? '还没有新的进展记录' : 'No new progress has been recorded');
  };
  // The goal line already names the latest task; saying its title again
  // under the conclusion only repeats it.
  const conclusion = accepted
    ? zh ? `已确认：${clip(readableRecord(written(accepted) || accepted.summary || accepted.title), 160)}`
      : `Confirmed: ${clip(readableRecord(written(accepted) || accepted.summary || accepted.title), 160)}`
    : latest
      ? statusKey(latest) === 'review_unavailable'
        ? zh ? '这次结果检查没有完成，暂时不能确认结果。' : 'The result check did not finish, so the result is not yet confirmed.'
        : zh ? '还没有经过检查确认的结果。' : 'There is no checked, confirmed result yet.'
      : zh ? "还没有结果" : "No result yet";
  const running = work.find((task) => ACTIVE.has(task.status));
  const waiting = byTime.find((task) => ["question", "held", "failed", "review_unavailable"].includes(statusKey(task)));
  const queued = [...work].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0)).find((task) => statusKey(task) === "pending");
  const current = running
    ? zh ? `正在处理：${clip(running.title, 70)}` : `Working on: ${clip(running.title, 70)}`
    : latest ? label(latest) : zh ? '可以开始新任务' : 'Ready for a new task';
  const next = running
    ? queued
      ? zh ? `待做任务：${clip(queued.title, 70)}` : `Queued next: ${clip(queued.title, 70)}`
      : zh ? '等当前工作完成，再查看检查结果。' : 'Wait for the current work to finish, then check the result.'
    : waiting && waiting === latest
      ? attentionSummary(waiting, events, zh, written(waiting)).next
      : queued
        ? zh ? `下一步：${clip(queued.title, 60)}` : `Next: ${clip(queued.title, 60)}`
        : accepted
          ? zh ? "可以查看结果，或继续提出要求。" : "Read the result, or tell me what to do next."
          : zh ? "还没有后续任务，可在对话里告诉我接下来做什么。" : "Nothing else is scheduled. Tell me what to do next in the conversation.";
  return { goal, current, conclusion, next };
}

/** Background explanation of recorded work, never an additional research result. */
export interface ReaderBrief {
  why: string;
  concept: { name: string; explanation: string; example: string; connection: string } | null;
  scope: string;
  next: string;
}

/** A sequence of background explanations, separate from the run's evidence. */
export interface ReaderLearningPath {
  question: string;
  steps: Array<{
    title: string;
    explanation: string;
    example: string;
    check: { question: string; answer: string };
  }>;
}

export interface CardCopy {
  copy_revision?: number;
  version?: number;
  model_revision?: string;
  title: string;
  summary: string;
  detail: string;
  /** Optional for existing cached cards created before presentation schema 10. */
  reader_brief?: ReaderBrief;
  learning_path?: ReaderLearningPath | null;
  foundation_ref?: { id: string; path: string; question: string; version: number };
  /** A teaching-text check is separate from the research task's review. */
  teaching_review?: {
    status?: 'accepted' | 'corrected' | 'unavailable';
    kind: 'model_teaching_review';
    reason?: string;
    reviewed_at?: number | null;
    review_version: number;
    reading_review?: {
      status: 'accepted' | 'corrected' | 'unavailable';
      kind: 'model_readability_review';
      reason?: string;
    };
  };
  generated_at: number;
  task_revision?: string;
  task_content_revision?: string;
  task_status?: string;
  event_ids?: string[];
  event_revisions?: string[];
  input_revision?: string;
}
export interface MapRelation {
  source: string;
  target: string;
  label: string;
  kind: "semantic";
}
export interface MapCopy {
  cache_revision?: number;
  version?: number;
  model_revision?: string;
  cards: Record<string, CardCopy>;
  relations: MapRelation[];
  available?: boolean;
  retry_after?: number;
  generation_error?: { code: string; message: string } | null;
}

export function mergeMapCopy(previous: MapCopy | undefined, result: MapCopy, requestedRevision?: string): MapCopy {
  const settingsChanged = previous?.model_revision && previous.model_revision !== requestedRevision;
  const cards = { ...previous?.cards };
  for (const [key, card] of Object.entries(result.cards)) {
    if (settingsChanged && cards[key]?.model_revision === previous.model_revision) continue;
    const old = cards[key];
    if (old?.version && (card.version ?? 0) < old.version) continue;
    if (!old || (card.copy_revision ?? 0) > (old.copy_revision ?? 0) ||
      ((card.copy_revision ?? 0) === (old.copy_revision ?? 0) &&
        (card.generated_at > old.generated_at ||
          (card.generated_at === old.generated_at && !old.input_revision)))) cards[key] = card;
  }
  const older = (result.cache_revision ?? 0) < (previous?.cache_revision ?? 0);
  return {
    ...previous,
    ...result,
    cards,
    ...((previous?.version != null || result.version != null)
      ? { version: Math.max(previous?.version ?? 0, result.version ?? 0) } : {}),
    cache_revision: Math.max(result.cache_revision ?? 0, previous?.cache_revision ?? 0),
    relations: (settingsChanged || older) && previous ? previous.relations : result.relations,
    available: result.available ?? true,
    ...(settingsChanged ? { model_revision: previous.model_revision, available: previous.available } : {}),
  };
}

export function needsCardCopy(
  card: CardRequest,
  data: Dataset,
  copy?: MapCopy,
  eventIndex?: Map<string, MapEvent>,
): boolean {
  const saved = copy?.cards[card.key];
  const task = data.tasks.find((t) => t.id === card.task_id);
  if (!saved || !task) return true;
  if ((saved.version ?? 0) < (copy?.version ?? 0)) return true;
  if (copy?.model_revision && saved.model_revision !== copy.model_revision) return true;
  const dynamic = [task.id, task.id + ":active", task.id + ":outcome"].includes(card.key);
  if (dynamic || !saved.task_content_revision || !task.content_revision) {
    if (task.revision && saved.task_revision !== task.revision) return true;
    if (dynamic && saved.task_status !== task.status) return true;
  } else if (saved.task_content_revision !== task.content_revision) return true;
  const ids = saved.event_ids || [];
  // A full-history summary may contain additional valid evidence when only
  // current progress is being viewed. Do not rewrite it with a poorer subset.
  return card.event_ids.some((id) => {
    const index = ids.indexOf(id);
    const event = eventIndex ? eventIndex.get(id) : data.events.find((e) => e.id === id);
    return index < 0 || (saved.event_revisions && event?.revision &&
      saved.event_revisions[index] !== event.revision);
  }) || (!dynamic && JSON.stringify(card.event_ids) !== JSON.stringify(ids));
}
export interface CardRequest {
  key: string;
  task_id: string;
  kind: string;
  event_ids: string[];
}
export interface CardReference {
  source: string;
  task_id: string;
  task_title: string;
  part?: number;
  step_id?: string;
  step_title?: string;
  event_ids: string[];
  team_id?: string;
  team_task_id?: string;
  /** Locale at quote time; the server renders its expansion to match. */
  lang?: string;
}
export const referenceText = (ref: CardReference) =>
  `[[Argus引用 ${JSON.stringify(ref)}]]\n`;
export function splitDraft(value: string) {
  const refs: CardReference[] = [];
  const lines = value.split("\n").filter((line) => {
    if (line.startsWith("[[Argus引用 ") && line.endsWith("]]")) {
      try {
        const ref = JSON.parse(line.slice(10, -2));
        if (
          typeof ref.task_id === "string" &&
          typeof ref.source === "string" &&
          typeof ref.task_title === "string" &&
          (ref.part === undefined ||
            (Number.isInteger(ref.part) && ref.part > 0)) &&
          (ref.step_title === undefined ||
            typeof ref.step_title === "string") &&
          (ref.step_id === undefined || typeof ref.step_id === "string") &&
          (ref.team_id === undefined || typeof ref.team_id === "string") &&
          (ref.team_task_id === undefined || typeof ref.team_task_id === "string") &&
          (ref.lang === undefined || typeof ref.lang === "string") &&
          Array.isArray(ref.event_ids) &&
          ref.event_ids.every((id: unknown) => typeof id === "string")
        ) {
          refs.push(ref);
          return false;
        }
      } catch {
        /* Keep malformed text editable. */
      }
    }
    return true;
  });
  return { refs, text: lines.join("\n").replace(/^\n+/, "") };
}
/** Ids of the latest main/review outcomes of a task, optionally as of a moment. */
function outcomeIds(data: Dataset, id: string, through = Infinity): string[] {
  const start = Math.max(
    -Infinity,
    ...data.events
      .filter(
        (e) =>
          e.item_id === id &&
          e.type === "life.mission.started" &&
          e.ts <= through,
      )
      .map((e) => e.ts),
  );
  return data.events
    .filter(
      (e) =>
        e.item_id === id &&
        e.ts >= start &&
        e.ts <= through &&
        ["round.main.completed", "round.review.completed"].includes(e.type),
    )
    .slice(-2)
    .map((e) => e.id);
}
/** One request per step of a task's sub-map, in reading order. */
export function stepRequests(data: Dataset, task: MapTask, steps: SubmapStep[]): CardRequest[] {
  return steps.map((s) => ({
    key: s.id,
    task_id: task.id,
    kind: s.kind,
    event_ids: [
      ...new Set([
        ...(s.kind === "result" ? outcomeIds(data, task.id, s.ts) : []),
        ...s.eventIds,
      ]),
    ].slice(-16),
  }));
}
export function requestsFor(
  data: Dataset,
  steps: SubmapStep[],
  focused: string | null,
): CardRequest[] {
  const focusedTask = data.tasks.find((t) => t.id === focused);
  const sorted = focusedTask
    ? [focusedTask, ...data.tasks.filter((t) => t.id !== focused)]
    : data.tasks;
  const roots = sorted.map((t) => ({
    key: t.id,
    task_id: t.id,
    kind: "task",
    event_ids: [
      ...new Set([
        ...outcomeIds(data, t.id),
        ...data.events
          .filter((e) => e.item_id === t.id)
          .slice(-2)
          .map((e) => e.id),
      ]),
    ],
  }));
  const children = focusedTask ? stepRequests(data, focusedTask, steps) : [];
  return [...roots.slice(0, 1), ...children, ...roots.slice(1)];
}
