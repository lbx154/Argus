import type { MapEvent, MapTask } from "./model";
import { isStageHold } from "./model";
import { humanizeHarnessNote } from "./submap";
import { errorText } from '../lib/format';

/** The newest explicit failure, with the task's own record when no event names one. */
function failureDetail(task: MapTask, events: MapEvent[]): string {
  const failure = events.filter((event) => event.item_id === task.id &&
    (event.status === "failed" || event.success === false || event.type.endsWith(".failed")))
    .sort((a, b) => b.ts - a.ts)[0];
  return failure?.reason || failure?.text || recordedDetail(task);
}

/** The failure summary is interface copy; the original reason stays in detail. */
export function attentionReason(task: MapTask, events: MapEvent[], zh: boolean): string {
  if (task.pending_question) return task.pending_question;
  const raw = failureDetail(task, events);
  const note = humanizeHarnessNote(raw, zh);
  return note.summary || errorText(raw, zh ? 'zh-CN' : 'en', 'agent_failed');
}

/** The recorded words, stripped of the harness prefix; shown as detail. */
function recordedDetail(task: MapTask): string {
  return (task.last_error || "").replace(/^manager stage (?:hold|rollback):\s*/i, "").trim();
}

/** What waits on the reader, as a short reason and next step in the reader's
 * language, with the agent's own (possibly other-language) words kept apart
 * as detail rather than shown as the headline. The next step is a plain
 * instruction with no "Next:" of its own; the brief shows it under that label.
 */
export function attentionSummary(task: MapTask, events: MapEvent[], zh: boolean):
  { reason: string; next: string; detail: string } {
  if (task.pending_question) return {
    reason: task.pending_question,
    next: zh ? "回答这个问题，团队才会继续。" : "Answer this question so the team can continue.",
    detail: "",
  };
  if (isStageHold(task)) {
    const detail = recordedDetail(task);
    return {
      reason: detail
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
    };
  }
  const reason = attentionReason(task, events, zh);
  const detail = failureDetail(task, events);
  return {
    reason: zh ? `执行失败：${reason}` : `Execution failed: ${reason}`,
    next: zh ? "查看原因后重试，或在对话里调整任务。" : "Check the reason, then retry or adjust the task in the conversation.",
    detail: detail && !reason.includes(detail) ? detail : "",
  };
}

export interface CardReference {
  source: string;
  task_id: string;
  task_title: string;
  part?: number;
  step_id?: string;
  step_title?: string;
  event_ids: string[];
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
