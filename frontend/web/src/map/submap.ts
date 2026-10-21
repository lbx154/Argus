import {
  ACTIVE,
  connectMap,
  latestMissionCompletion,
  type MapEvent,
  type MapTask,
  type MapGraph,
  type MapLink,
  type WorkStep,
} from "./model";
import { layoutGraph } from "./graphLayout";
import { completionScope } from "./status";
import { humanizeHarnessNote, TECHNICAL_MARKER_PREFIX } from "../lib/harnessNotes";
import { visibleAgentText } from "../../../core/src/events";
import { isPauseStatus, parkReason, stopClause } from "../../../core/src/missionOutcome";
import { plainStopSentence } from "../lib/plainStatus";
export { humanizeHarnessNote, TECHNICAL_MARKER } from "../lib/harnessNotes";

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
  completionScope?: string;
  historyCount?: number;
  historyExpanded?: boolean;
  /** Where the task stands in one line: what the review asked for, the round
   * under way. Computed over every step, so each part of a long task carries it. */
  conclusion?: string;
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
  source: "task" | "event" | "interval";
  eventIds: string[];
  revision?: string;
  completionScope?: string;
  /** Tool activity recorded while this step was under way: how many actions
   * its card can mention. The actions themselves are in `detail`. */
  workCount?: number;
  /** Building only: a stretch of tool activity not yet placed in its step. */
  segment?: boolean;
  work?: StepWork;
  closed?: boolean;
}
/** The tool activity folded into a step, kept apart until the step's own
 * record is final (a round's closing event rewrites its detail). */
interface StepWork {
  steps: WorkStep[];
  overflow: number;
  narrations: string[];
  note: string;
  /** A title the record itself calls for, ahead of anything derived. */
  title: string;
  /** The work of a single agent answering a turn, not of a role in a round. */
  single: boolean;
}
export const STEP_KINDS: StepKind[] = [
  "plan",
  "execution",
  "review",
  "revision",
  "result",
];

export const noDetails = (zh: boolean) =>
  zh ? "这一步还没有留下记录" : "Nothing has been written down for this step yet.";


function clipSentence(value: string, limit = 140): string {
  const text = value.replace(/\s+/g, " ").trim();
  if (!text) return "";
  const match = text.match(/^.*?[。！？!?.](?=\s|$)/);
  const sentence = (match ? match[0] : text).trim();
  return sentence.length > limit
    ? `${sentence.slice(0, limit - 1).trimEnd()}…`
    : sentence;
}

function titleClause(prose: string): string {
  const clause = prose
    .replace(/\s+/g, " ")
    .trim()
    .split(/[。！？；;：:，,\n]|\.(?=\s|$)|[!?](?=\s|$)/)[0]
    ?.trim();
  // Too-short clauses are fragments ("First", "然后"), not titles.
  return clause && clause.length >= 6 && clause.length <= 40 ? clause : "";
}

/** Keep scientific prose visible while hiding the runner's control footer. */
export function readableRecord(value: string | undefined | null): string {
  return visibleAgentText(value)
    .split(/\r?\n/)
    .filter(
      (line) =>
        // A line that is one JSON object is an instruction to the harness
        // ({"wait_for": "subagent", ...}), not something said to a reader.
        !/^\{\s*"[\w-]+"\s*:.*\}$/.test(line.trim()),
    )
    .map((line) =>
      line
        .replace(/^\s*(?:RESULT|SUMMARY)\s*=\s*/i, "")
        .replace(/^\s*NEXT_ACTION\s*=\s*/i, ""),
    )
    .join("\n")
    .trim();
}

/** What a structural record means, for the steps that carry no prose of their
 * own. Only the events whose meaning is fixed by their type get a sentence;
 * a silent round still says that nothing was written down. */
function describeRecord(event: MapEvent, status: string, zh: boolean): string {
  switch (event.type) {
    case "life.planner.task_added":
      return zh ? "规划者把这项任务列入了计划。" : "The Planner added this task to the plan.";
    case "life.mission.started":
      return zh ? "工程师接手了这项任务，开始动手。" : "The Engineer picked up this task and began working.";
    case "life.phase.started":
    case "round.review.started":
      return event.role === "reviewer"
        ? zh ? "审阅者打开了工程师的工作，开始核查。" : "The Reviewer opened the Engineer's work to check it."
        : "";
    case "life.mission.completed": {
      const reason = parkReason(status, event.park_reason);
      return completionScope(event, zh) || (status === "done"
        ? zh ? "任务完成，结果已经记录在案。" : "The task was finished; its result is on record."
        : isPauseStatus(status)
          ? reason ? plainStopSentence(reason, zh ? "zh-CN" : "en")
            : zh ? "这项任务的工作暂停了。" : "Work on this task was paused."
          : zh ? "任务到此结束。" : "The task ended here.");
    }
    case "life.mission.failed":
      return zh ? "任务没有达到目标就结束了。" : "The task ended without reaching its goal.";
    default:
      return "";
  }
}

const STEP_GLYPH = /^(?:[⚙✎↳$…∴▸]|✗ \$)\s*/u;
const shorten = (value: string, limit: number) =>
  value.length > limit ? `${value.slice(0, limit - 1).trimEnd()}…` : value;

/** The one argument a reader wants to see for a tool call: a path, a pattern, a URL. */
function stepArgument(label: string): string {
  const raw = label.slice(label.indexOf(":") + 1).trim();
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>;
      for (const key of ["path", "file", "file_path", "pattern", "query", "url", "command", "paths"]) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) return value.trim();
        if (Array.isArray(value) && typeof value[0] === "string") return value[0];
      }
      const first = Object.values(record).find((value) => typeof value === "string" && value.trim());
      return typeof first === "string" ? first.trim() : "";
    }
  } catch {
    // Not JSON, or JSON the recorder cut short.
  }
  if (!/^[{[]/.test(raw)) return raw;
  // Cut-short JSON still names what was touched; a reader is never shown the braces.
  return /"(?:path|file|file_path|pattern|query|url|command)"\s*:\s*"([^"]+)/.exec(raw)?.[1] ?? "";
}

type StepVerb = "read" | "search" | "fetch" | "edit" | "run" | "other";
function stepVerb(step: WorkStep): StepVerb {
  const name = (step.tool || step.label.replace(STEP_GLYPH, "").split(":")[0] || "").trim().toLowerCase();
  if (step.kind === "command_execution") return "run";
  if (step.kind === "file_change") return "edit";
  if (/^(view|read|cat|open|read_file|str_replace_editor)$/.test(name)) return "read";
  if (/^(rg|grep|search|find_text|semantic_search|codebase_search|web_search|search_web)$/.test(name)) return "search";
  if (/^(glob|find|list|ls|list_dir|find_files)$/.test(name)) return "search";
  if (/^(web_fetch|fetch|http_get|browse)$/.test(name)) return "fetch";
  if (/^(apply_patch|edit|write|create|write_file|edit_file|replace_in_file|str_replace)$/.test(name)) return "edit";
  if (/^(bash|shell|sh|execute|terminal|run)$/.test(name)) return "run";
  return "other";
}

/** One tool call as a short plain phrase — what was done, to what. */
export function stepPhrase(step: WorkStep, zh: boolean): string {
  const tool = (step.tool || "").trim();
  const label = step.label.replace(STEP_GLYPH, "").trim();
  // The Copilot desktop agent titles each call in plain words; the streaming
  // runners give a bare tool name plus its arguments.
  const titled = tool && /\s/.test(tool) && tool.length > 8;
  const argument = shorten(stepArgument(label), 72);
  const command = shorten(label, 72);
  const verb = stepVerb(step);
  const phrase = titled
    ? tool
    : verb === "run"
      ? zh ? `运行命令 \`${command}\`` : `Ran \`${command}\``
      : verb === "read"
        ? zh ? `查看 ${argument || (zh ? "一份文件" : "")}` : `Read ${argument || "a file"}`
        : verb === "search"
          ? zh ? `查找 ${argument || "内容"}` : `Looked for ${argument || "something"}`
          : verb === "fetch"
            ? zh ? `读取网页 ${argument || ""}`.trim() : `Fetched ${argument || "a page"}`
            : verb === "edit"
              ? zh ? `修改文件 ${argument || ""}`.trim() : `Edited ${argument || "a file"}`
              : shorten(/^[^:{]*:\s*[{[]/.test(label) ? label.slice(0, label.indexOf(":")) : label, 80);
  return step.status === "failed" ? `${phrase}${zh ? "（失败）" : " (failed)"}` : phrase;
}

/** "查看了 3 个文件，运行了 2 条命令" — the shape of a segment's work at a glance. */
export function stepsSummary(steps: WorkStep[], overflow: number, zh: boolean): string {
  const counts = new Map<StepVerb, number>();
  steps.forEach((step) => counts.set(stepVerb(step), (counts.get(stepVerb(step)) ?? 0) + 1));
  const parts: string[] = [];
  const say = (verb: StepVerb, zhWord: string, enOne: string, enMany: string) => {
    const n = counts.get(verb);
    if (n) parts.push(zh ? `${zhWord}${n}${verb === "run" ? "条命令" : verb === "fetch" ? "个网页" : "处"}` : `${n} ${n === 1 ? enOne : enMany}`);
  };
  say("read", "查看了", "file read", "files read");
  say("search", "查找了", "search", "searches");
  say("fetch", "读取了", "page fetched", "pages fetched");
  say("edit", "修改了", "file edited", "files edited");
  say("run", "运行了", "command run", "commands run");
  const other = counts.get("other");
  if (other) parts.push(zh ? `${other}次其他操作` : `${other} other ${other === 1 ? "action" : "actions"}`);
  if (overflow > 0) parts.push(zh ? `另有 ${overflow} 步未列出` : `${overflow} more not listed`);
  return parts.length ? (zh ? `${parts.join("，")}。` : `${parts.join(", ")}.`) : "";
}

/** A call reported twice — started, then failed — with no id to tell them
 * apart is one step carrying the later status, not two. */
export function mergeReports(steps: WorkStep[]): WorkStep[] {
  const out: WorkStep[] = [];
  for (const step of steps) {
    const last = out[out.length - 1];
    const sameCall = last && last.status === "running" && step.status && step.status !== "running"
      && (last.call_id && step.call_id ? last.call_id === step.call_id
        : !last.call_id && !step.call_id && last.kind === step.kind && last.label === step.label && (last.tool ?? "") === (step.tool ?? ""));
    if (sameCall) out[out.length - 1] = { ...last, status: step.status, ...(step.call_id ? { call_id: step.call_id } : {}) };
    else out.push(step);
  }
  return out;
}

/** A stretch of tool activity: the agent's own words for what it was doing,
 * then the calls it made. It is not a stage of the task. `buildSubmap` places
 * it inside the round of work or review it happened in; it stands as a step of
 * its own only where there is no such round (a single-agent turn). */
function workSegmentStep(event: MapEvent, zh: boolean): SubmapStep {
  const steps = mergeReports(event.steps ?? []);
  const narration = readableRecord(event.text);
  const missingDetails = event.tool_details_recorded === false;
  const note = missingDetails
    ? zh ? "执行日志确认发生过工具活动，但这一轮没有记录详细工具步骤。"
      : "Execution logs confirm tool activity, but detailed tool steps were not recorded for this turn."
    : "";
  return standaloneWork({
    id: event.id,
    // The Reviewer's own reading and checking belongs to the review.
    kind: event.role === "reviewer" ? "review" : "execution",
    title: "",
    detail: "",
    // A tool call that failed along the way is shown on its line. It does not
    // make the step a failure: a missed grep is not a verdict on the work.
    status: event.status || "recorded",
    ts: event.ts,
    source: event.association === "single_active_window" ? "interval" : "event",
    eventIds: [event.id],
    segment: true,
    work: {
      steps: [...steps], overflow: event.overflow ?? 0, narrations: narration ? [narration] : [], note,
      title: missingDetails ? zh ? "已记录的执行" : "Recorded execution" : "",
      single: event.role === "manager",
    },
  }, zh);
}

/** A step's actions for the reader who opens it: enough to see what kind of
 * work it was. The full trail is in the conversation; a hundred lines of it
 * here pushed the step's own record out of sight. */
const WORK_LINES = 8;
const workLines = (work: StepWork, zh: boolean) => {
  const rest = Math.max(0, work.steps.length - WORK_LINES) + work.overflow;
  return [
    ...work.steps.slice(0, WORK_LINES).map((step) => `· ${stepPhrase(step, zh)}`),
    ...(rest > 0 ? [zh ? `· 另有 ${rest} 步未列出` : `· ${rest} more not listed`] : []),
  ];
};

/** Tool activity with no round to belong to, told as a step of its own. */
function standaloneWork(step: SubmapStep, zh: boolean): SubmapStep {
  const work = step.work!;
  const narration = work.narrations.join("\n\n");
  step.title = work.title
    || titleClause(work.narrations[0] ?? "")
    || (work.single
      ? zh ? "Argus 动手查证" : "Argus did the work"
      : work.steps.length
        ? zh ? `做了 ${work.steps.length} 步操作` : `${work.steps.length} steps of work`
        : zh ? "说明了接下来要做什么" : "Said what comes next");
  step.summary = clipSentence(work.narrations[0] ?? "") || stepsSummary(work.steps, work.overflow, zh) || undefined;
  step.detail = [work.note, narration, workLines(work, zh).join("\n")].filter(Boolean).join("\n\n") || noDetails(zh);
  step.workCount = work.steps.length + work.overflow || undefined;
  return step;
}

/** A round's step with the tool activity recorded inside it. The round's own
 * record stays the story; what the agent said along the way and the calls it
 * made follow it, for the reader who opens the step. */
function withWork(step: SubmapStep, zh: boolean): SubmapStep {
  const work = step.work;
  if (!work || step.segment) return step;
  const record = step.detail === noDetails(zh) ? "" : step.detail;
  // A round's closing record usually repeats the last thing the agent said.
  const along = work.narrations.filter((text) => !record.includes(text.slice(0, 60)));
  const count = work.steps.length + work.overflow;
  const shape = stepsSummary(work.steps, 0, zh);
  const actions = count
    ? [zh ? `这一步里做的操作（${count} 步）：${shape}` : `What was done in this step (${count} actions): ${shape}`,
      workLines(work, zh).join("\n")].join("\n")
    : "";
  // Until the round's own record arrives, the latest thing said is the news.
  const latest = work.narrations[work.narrations.length - 1];
  if (!step.closed && latest) {
    step.title = titleClause(latest) || step.title;
    step.summary = clipSentence(latest) || step.summary;
  }
  step.detail = [work.note, record, along.join("\n\n"), actions].filter(Boolean).join("\n\n") || noDetails(zh);
  step.workCount = count || undefined;
  return step;
}

/** Where a finished task ended up, when its own record says nothing. */
function outcomeSentence(status: string, zh: boolean): string {
  switch (status) {
    case "done": return zh ? "这项任务已经完成。" : "This task was completed.";
    case "failed": return zh ? "这项任务没有达到目标。" : "This task did not reach its goal.";
    case "cancelled":
    case "aborted": return zh ? "这项任务被取消了。" : "This task was cancelled.";
    case "skipped": return zh ? "这项任务被跳过了。" : "This task was skipped.";
    case "superseded": return zh ? "这项任务被一个新的计划取代了。" : "A new plan took the place of this task.";
    default: return "";
  }
}

/** What was said about a task by those not working on it: the question put
 * to the operator and its answer, the Observer's remarks, the Manager's
 * direction. Each is a step of its own, in the words recorded. */
const SAID: Record<string, { kind: StepKind; title: [string, string] }> = {
  "life.operator_question.pending": { kind: "plan", title: ["向你提问", "A question for you"] },
  "life.operator_question.answered": { kind: "plan", title: ["你的答复", "Your answer"] },
  "life.observer.note": { kind: "revision", title: ["监督者的提醒", "The Observer's note"] },
  "life.manager.supervision.applied": { kind: "plan", title: ["经理的督导", "The Manager's direction"] },
};

/** Display observations, including their evidence strength; never fill a missing stage with success. */
export function buildSubmap(
  task: MapTask,
  events: MapEvent[],
  zh: boolean,
): SubmapStep[] {
  const turn = task.kind === "turn";
  const rows: SubmapStep[] = [
    {
      id: `${task.id}:brief`,
      kind: "plan",
      title: turn
        ? zh ? (task.turn_kind === 'qa' ? '你提出的问题' : "你提出的要求") : "What you asked"
        : zh ? "任务目标" : "Task goal",
      detail: task.objective || task.title,
      status: "recorded",
      source: "task",
      eventIds: [],
    },
  ];
  const seen = new Set<string>();
  let episode = 0;
  let lastReview: MapEvent | undefined;
  const sorted = events
    .filter((e) => e.item_id === task.id)
    .sort((a, b) => a.ts - b.ts);
  for (const e of sorted) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    if (e.type === "work.segment") {
      rows.push({ ...workSegmentStep(e, zh), episode });
      continue;
    }
    if (e.type === "turn.replied") {
      const answer = readableRecord(e.text);
      rows.push({
        id: e.id,
        kind: "result",
        title: zh ? "Argus 的回答" : "What Argus answered",
        summary: clipSentence(answer) || undefined,
        detail: answer || noDetails(zh),
        status: e.status || "done",
        ts: e.ts,
        source: "event",
        eventIds: [e.id],
      });
      continue;
    }
    const said = SAID[e.type];
    if (said) {
      const record = readableRecord(e.text);
      rows.push({
        id: e.id,
        kind: said.kind,
        // The Manager may answer in the operator's place; that is not the operator's answer.
        title: e.type.endsWith(".answered") && e.role !== "operator"
          ? zh ? "Argus 代你答复" : "Argus answered for you"
          : said.title[zh ? 0 : 1],
        summary: clipSentence(record) || undefined,
        detail: record || noDetails(zh),
        status: "recorded",
        ts: e.ts,
        episode,
        source: e.association === "single_active_window" ? "interval" : "event",
        eventIds: [e.id],
      });
      continue;
    }
    if (e.type === "life.mission.started") {
      episode++;
      lastReview = undefined;
    }
    if (e.type === "round.review.completed") lastReview = e;
    // An Engineer's handoff or self-check closes its round where a review
    // would; no Reviewer took part, so it is part of the Engineer's round.
    const engineerVerdict = e.type.includes("review") && e.role === "engineer";
    const kind: StepKind | null =
      (e.type.includes("review") && !engineerVerdict) ||
      (e.type === "life.phase.started" && e.role === "reviewer")
        ? "review"
        : e.type === "life.planner.task_added"
          ? "plan"
          : e.type === "life.mission.completed" ||
              e.type === "life.mission.failed"
            ? "result"
            : e.type === "round.start" ||
                e.type === "round.main.completed" ||
                engineerVerdict ||
                e.type === "life.mission.started" ||
                e.type === "life.phase.started"
              ? "execution"
              : null;
    if (!kind) continue;
    const round = e.round_index;
    const finished =
      e.type.endsWith(".completed") || e.type.endsWith(".failed");
    const reviewSkipped = kind === "review" && e.review_skipped === true;
    const reviewUnavailable = e.backend_unavailable === true || (kind === "result" && lastReview?.backend_unavailable === true);
    const scope = reviewUnavailable ? "" : completionScope(e, zh);
    const status =
      (reviewUnavailable ? "review_unavailable" : reviewSkipped ? "skipped" : e.status === "error" ? "failed" : e.status) ||
      (e.success === false || e.type.endsWith(".failed")
        ? "failed"
        : e.success === true
          ? "done"
          : finished || e.type === "life.planner.task_added" || e.type === "life.mission.started"
            // Being put on the plan and being taken up happen at a moment.
            // Only a round can be under way, and its step says so while it is.
            ? "recorded"
            : "started");
    const recorded = humanizeHarnessNote(e.text || "", zh);
    // A round stopped rather than failed says by whom or what, as its record names it.
    const stopped = stopClause(e.stop_kind, zh);
    const note = stopped ? { ...recorded, summary: zh
      ? `${stopped}，这一轮没有做完；这不是对工作本身的评价`
      : `This round was cut short because ${stopped}; this says nothing about the work itself` } : recorded;
    const raw = String(e.text || "");
    const cut = note.receipt ? raw.lastIndexOf(note.receipt) : -1;
    const prose = readableRecord(cut >= 0 ? raw.slice(0, cut) : raw);
    const reviewVerdictTitle =
      status === "done"
        ? zh ? "审阅通过" : "Review passed"
        : status === "continue"
          ? zh ? "审阅：要求再改一轮" : "Review: another pass asked for"
          : ["blocked", "replan", "replan_requested"].includes(status)
            ? zh ? "审阅：建议调整方向" : "Review: a change of course asked for"
            : status === "failed"
              ? zh ? "审阅未通过" : "Review not passed"
              : "";
    // Harness plumbing never names a step; only research prose does.
    const interrupted = Boolean(stopped);
    const clause = kind === "execution" && !note.summary ? titleClause(prose) : "";
    const title = reviewUnavailable
      ? zh ? "当次审查异常" : "Review error on this attempt"
      : interrupted
      ? zh ? "这一轮没做完就被停下" : "Stopped before the round could finish"
      : reviewSkipped
        ? zh ? "本轮未审阅" : "No review this round"
        : kind === "review"
          ? finished
            ? reviewVerdictTitle || (zh ? "审阅意见" : "Review")
            : zh
              ? "开始审阅"
              : "Review started"
          : kind === "result"
            ? scope
              ? zh ? "本次执行已结束" : "This execution ended"
              : zh ? "执行结果" : "Result of the work"
            : kind === "plan"
              ? zh
                ? "列入计划"
                : "Taken into the plan"
              : clause ||
                (note.kind === "round"
                  ? zh
                    ? "新一轮开始"
                    : "A new round began"
                  : e.type === "life.mission.started"
                    ? zh
                      ? "开始执行"
                      : "Work started"
                    : e.type === "round.main.completed"
                      ? zh
                        ? "本轮执行记录"
                        : "This round's work"
                      : zh
                        ? "一次执行尝试"
                        : "An attempt at the work");
    const nextNote = humanizeHarnessNote(e.next_action || "", zh);
    const described = prose || note.summary ? "" : describeRecord(e, status, zh);
    rows.push({
      id: e.id,
      kind,
      title,
      summary: reviewUnavailable
        ? zh ? "审查器未能给出判断，不是审阅否决。" : "The Reviewer could not return a judgment; this was not a rejection."
        : scope || note.summary || clipSentence(prose) || described || undefined,
      detail: [
        scope,
        prose || note.summary || (!scope ? described || noDetails(zh) : ""),
        scope && e.stage_certification === "intentionally_skipped"
          ? zh ? "本阶段认证被有意跳过；这不代表最终验收通过。"
            : "Stage certification was intentionally skipped; this is not final acceptance."
          : "",
        reviewSkipped && e.next_action && !nextNote.summary
          ? `${zh ? "接下来" : "What happens next"}: ${e.next_action}`
          : "",
        note.receipt
          ? `${zh ? "——技术记录：" : "— technical record: "}${note.receipt.replace(TECHNICAL_MARKER_PREFIX, "")}`
          : "",
      ].filter(Boolean).join("\n\n"),
      status: scope && status === "done" ? "recorded" : status,
      completionScope: scope || undefined,
      closed: finished,
      ts: e.ts,
      round,
      episode,
      source: e.association === "single_active_window" ? "interval" : "event",
      eventIds: [e.id],
    });
    if (
      !reviewSkipped && !interrupted && e.next_action &&
      ["continue", "blocked", "replan", "replan_requested"].includes(
        e.status || "",
      )
    ) {
      rows.push({
        id: `${e.id}:next`,
        kind: "revision",
        title: zh ? "建议的修改" : "Changes asked for",
        detail: nextNote.summary || e.next_action,
        status: "requested",
        ts: e.ts,
        round,
        episode,
        source: e.association === "single_active_window" ? "interval" : "event",
        eventIds: [e.id],
      });
    }
  }
  if (task.turn_kind === 'qa' && !rows.some(r => r.kind === 'result')) {
    rows.push({ id: `${task.id}:answer`, kind: 'result',
      title: ACTIVE.has(task.status) ? (zh ? '正在回答' : 'Answering') : (zh ? '回答状态' : 'Answer status'),
      detail: task.summary || (task.status === 'cancelled' ? (zh ? '本次回答已中断。' : 'This answer was interrupted.') : ''),
      status: task.status, source: 'task', eventIds: [] });
  }
  if (task.turn_kind !== 'qa' && !rows.some((r) => r.kind === "execution") && ACTIVE.has(task.status))
    rows.push({
      id: `${task.id}:active`,
      kind: "execution",
      title: zh ? "正在进行的工作" : "Work under way",
      detail: task.summary || "",
      status: task.status,
      source: "task",
      eventIds: [],
    });
  if (
    !rows.some((r) => r.kind === "result") &&
    ["done", "failed", "aborted", "cancelled", "skipped", "superseded"].includes(task.status)
  )
    rows.push({
      id: `${task.id}:outcome`,
      kind: "result",
      title: zh ? "任务的最终状态" : "Where this task ended up",
      detail: task.summary || outcomeSentence(task.status, zh),
      status: task.status,
      source: "task",
      eventIds: [],
    });
  // Collapse start/progress/completion into one readable node only within an
  // explicitly numbered round and the same observed mission episode.
  const merged: SubmapStep[] = [];
  const groups = new Map<string, SubmapStep>();
  // The round of work and the round of review under way, which the tool
  // activity recorded meanwhile belongs to.
  const underWay = new Map<StepKind, SubmapStep>();
  const absorb = (host: SubmapStep, row: SubmapStep) => {
    const work = (host.work ??= { steps: [], overflow: 0, narrations: [], note: "", title: "", single: false });
    work.steps.push(...row.work!.steps);
    work.overflow += row.work!.overflow;
    work.narrations.push(...row.work!.narrations.filter((text) => !work.narrations.includes(text)));
    work.note ||= row.work!.note;
    host.eventIds.push(...row.eventIds);
    if (row.source === "interval") host.source = "interval";
  };
  for (const row of rows) {
    if (row.segment) {
      // A task is read as its stages: what was asked, a round of work, its
      // review, what the review asked for, the next round, the outcome. Forty
      // tool calls are what happened inside one of those stages, not a stage,
      // and a card for each stretch of them buried the stages and left the
      // links between cards with nothing to say but "then".
      const round = underWay.get(row.kind);
      const tail = merged[merged.length - 1];
      const host = round && round.episode === row.episode ? round
        : tail?.segment && tail.kind === row.kind ? tail : undefined;
      if (host) {
        absorb(host, row);
        if (host.segment) standaloneWork(host, zh);
      } else merged.push({ ...row, eventIds: [...row.eventIds] });
      continue;
    }
    const mergeable =
      row.round != null && ["execution", "review"].includes(row.kind);
    const key = `${row.episode}:${row.round}:${row.kind}`;
    const previous = mergeable ? groups.get(key) : undefined;
    if (previous) {
      // Titles/summaries are display-only: the latest observation of the merged
      // round carries the most complete record, so it names the node.
      previous.title = row.title;
      // A closing event with empty text must not erase the round's story.
      previous.summary = row.summary ?? previous.summary;
      previous.detail = row.detail;
      previous.status = row.status;
      previous.closed = previous.closed || row.closed;
      previous.eventIds.push(...row.eventIds);
      if (row.source === "interval") previous.source = "interval";
    } else {
      const copy = { ...row, eventIds: [...row.eventIds] };
      merged.push(copy);
      if (mergeable) {
        groups.set(key, copy);
        underWay.set(copy.kind, copy);
      }
    }
  }
  return merged.map((step) => {
    const { work: _work, segment: _segment, closed: _closed, ...shown } = withWork(step, zh);
    return shown;
  });
}

/** The task's standing in one line, for its card and its heading: what the
 * last review asked for and the round under way. */
export function taskConclusion(steps: SubmapStep[], zh: boolean, active = true): string {
  const parts: string[] = [];
  const review = [...steps].reverse().find((step) => step.kind === "review"
    && ["done", "continue", "blocked", "replan", "replan_requested", "failed"].includes(step.status));
  if (review) {
    parts.push(review.status === "done" ? zh ? "审阅通过" : "review passed"
      : review.status === "continue" ? zh ? "审阅要求再改一轮" : "the review asked for another pass"
      : review.status === "failed" ? zh ? "审阅没通过" : "review not passed"
      : zh ? "审阅建议换个方向" : "the review asked for a change of course");
  }
  const round = active ? [...steps].reverse().find((step) => step.kind === "execution"
    && step.round != null && step.status === "started") : undefined;
  if (round) parts.push(zh ? `第 ${round.round} 轮进行中` : `round ${round.round} under way`);
  if (!parts.length) return "";
  return (zh ? "当前结论：" : "So far: ") + parts.join(zh ? "；" : "; ");
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
  const rounds = effectiveRounds(steps);
  let previousRounds: number[] = [];
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
    const sliceRounds = roundsIn(rounds.slice(start, end));
    const title = columnTitle(slice, sliceRounds, previousRounds, col === 0 && offset === 0, col === count - 1, zh, fallback);
    previousRounds = sliceRounds;
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

/** The round each step belongs to. A step recorded without a round number
 * belongs to the round under way when it was recorded. Tool activity is
 * already inside its round's step (buildSubmap), so what is left unnumbered
 * before the first round is the task being taken up, which is part of setting
 * out. Planning steps and the outcome of the whole task belong to no round. */
function effectiveRounds(steps: SubmapStep[]): (number | undefined)[] {
  let previous: number | undefined;
  return steps.map((step) => {
    if (typeof step.round === "number") return (previous = step.round);
    if (step.kind === "result" || step.kind === "plan") return undefined;
    // Taking the task up comes before its first round, not inside it.
    return previous;
  });
}

function roundsIn(rounds: (number | undefined)[]): number[] {
  return [...new Set(rounds.filter((r): r is number => typeof r === "number"))].sort((a, b) => a - b);
}

/** A column is headed by the round of work it holds, so a reader sees the
 * rhythm of the research (a round of work, its review, the next round) instead
 * of a running count of cells. Columns before any round are the setting out,
 * a trailing column without rounds is the outcome. */
function columnTitle(
  steps: SubmapStep[],
  rounds: number[],
  previousRounds: number[],
  first: boolean,
  last: boolean,
  zh: boolean,
  fallback: string,
): string {
  if (!rounds.length) {
    if (first && last && steps.length > 1 && steps[0].kind === "plan" && steps[steps.length - 1].kind === "result")
      return zh ? "起点 → 结果" : "Setting out → Outcome";
    if (first && steps.some((s) => s.kind === "plan")) return zh ? "起点" : "Setting out";
    if (last && steps.some((s) => s.kind === "result")) return zh ? "结果" : "Outcome";
    return fallback;
  }
  const lo = rounds[0], hi = rounds[rounds.length - 1];
  const continued = rounds.length === 1 && previousRounds.length > 0 &&
    previousRounds[previousRounds.length - 1] === lo;
  let label = lo === hi
    ? zh ? `第 ${lo} 轮` : `Round ${lo}`
    : zh ? `第 ${lo}–${hi} 轮` : `Rounds ${lo}–${hi}`;
  // The same round runs on into this column: say which part of it is here.
  if (continued)
    label = steps[0].kind === "review"
      ? zh ? `${label} · 审阅` : `${label} · review`
      : zh ? `${label} · 续` : `${label} · cont.`;
  // A column that also holds the planning before the first round, or the
  // outcome after the last, says so.
  if (first && steps[0].kind === "plan") label = zh ? `起点 · ${label}` : `Setting out · ${label}`;
  if (last && steps[steps.length - 1].kind === "result") label = zh ? `${label} · 结果` : `${label} · Outcome`;
  return label;
}

export function frameForSubmap(layout: Pick<SubmapLayout, "width" | "height" | "stacked">) {
  // A stacked card keeps one width, so its summary reads at the same size as
  // its neighbours' whatever its height.
  const scale = layout.stacked
    ? 900 / layout.width
    : Math.max(
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
    let label = zh ? "接着" : "then";
    let explanation = zh
      ? "同一任务的相邻观察，未确认直接因果或执行依赖。"
      : "Adjacent observations of the same task; no causal dependency is asserted.";
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
    } else if (
      source.kind === "execution" &&
      target.kind === "review" &&
      sameRound
    ) {
      relation = "review";
      label = zh ? "提交审阅" : "sent for review";
      explanation = zh
        ? "同一个任务、同一执行段、同一轮次的执行与审查记录。"
        : "Execution and review belong to the same task, episode and numbered round.";
    } else if (
      source.kind === "review" &&
      target.kind === "revision" &&
      target.eventIds.some((id) => source.eventIds.includes(id))
    ) {
      relation = "revision";
      label = zh ? "提出修改" : "asked to change";
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
      label = zh ? "进入下一轮" : "next round";
      explanation = zh
        ? "修订建议之后出现了同一任务的下一轮执行；不表示建议的全部内容已被采纳。"
        : "A later round follows the revision request; this does not certify every requested change was applied.";
    } else if (
      source.kind === "review" &&
      target.kind === "execution" &&
      sameEpisode &&
      source.round != null &&
      target.round != null &&
      target.round > source.round
    ) {
      relation = "next_attempt";
      label = zh ? "进入下一轮" : "next round";
      explanation = zh
        ? "审阅之后出现了同一任务的下一轮执行。"
        : "A later round of the same task follows the review.";
    } else if (
      source.kind === "execution" &&
      target.kind === "execution" &&
      source.round == null &&
      target.round != null
    ) {
      label = zh ? `进入第 ${target.round} 轮` : `round ${target.round} begins`;
      explanation = zh
        ? "任务开始执行后记录的一轮工作。"
        : "A round of work recorded after the task was taken up.";
    } else if (source.kind === "result" && target.kind === "execution") {
      label = zh ? "再次开始" : "taken up again";
      explanation = zh
        ? "上一次执行结束之后，这项任务又被接手。"
        : "The task was taken up again after an earlier execution ended.";
    } else if (target.kind === "result" && target.source === "task") {
      relation = "snapshot";
      label = zh ? "最终状态" : "ended as";
      explanation = zh
        ? "任务状态记录；部分执行过程可能缺失。"
        : "Links to the captured state of this task; intermediate records may be missing.";
    } else if (
      target.kind === "result" &&
      ["review", "execution", "revision"].includes(source.kind)
    ) {
      relation = "outcome";
      label = zh ? "形成结果" : "led to";
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
  links = connectMap(graph, [], zh),
  expandedMissions?: ReadonlySet<string>,
  narrow = false,
) {
  const cards: MapCard[] = [];
  const layouts: Record<string, SubmapLayout> = {};
  const continuations: MapLink[] = [];
  const lastCard = new Map<string, string>();
  for (const [ordinal, task] of graph.tasks.entries()) {
    const steps = buildSubmap(task, events, zh);
    const scope = task.status === "done" ? completionScope(latestMissionCompletion(task, events), zh) : "";
    const conclusion = task.kind === "turn" ? "" : taskConclusion(steps, zh, ACTIVE.has(task.status));
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
        completionScope: scope || undefined,
        conclusion: conclusion || undefined,
      });
      layouts[id] = layoutSubmap(task, events, zh, slice, start, narrow);
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
  const allCards = cards.slice();
  const visibleCard = new Map<string, string>();
  if (expandedMissions) {
    cards.length = 0;
    for (const card of allCards) {
      const expanded = expandedMissions.has(card.task.id);
      visibleCard.set(card.id, expanded ? card.id : card.task.id);
      if (!expanded && card.part !== card.partCount) continue;
      const id = expanded ? card.id : card.task.id;
      layouts[id] = layouts[card.id];
      cards.push({
        ...card, id,
        historyCount: card.partCount - 1,
        historyExpanded: expanded,
      });
    }
  }
  const resolve = (id: string) => visibleCard.get(id) ?? id;
  const frames = Object.fromEntries(
    cards.map(({ id }) => [id, frameForSubmap(layouts[id])]),
  );
  const displayLinks = [
    ...links.map((e) => ({
      ...e,
      source: resolve(lastCard.get(e.source)!),
      target: resolve(e.target),
    })),
    ...continuations.filter((link) => resolve(link.source) !== resolve(link.target)),
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
  return { cards, allCards, links: displayLinks, layouts, positions, frames, structure };
}
