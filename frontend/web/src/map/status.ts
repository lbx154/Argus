import type { MapEvent } from "./model";
import { roleActionLabel } from '../lib/taskLanguage';

export function completionScope(event: MapEvent | undefined, zh: boolean): string {
  if (event?.type !== "life.mission.completed" ||
    !(event.overall_complete === false || event.campaign_continues === true)) return "";
  if (event.outcome?.execution_status === "completed" && event.outcome.review_status === "done") return "";
  return zh
    ? `当次执行已结束；当时总体目标尚未完成${event.campaign_continues === true ? "，仍需后续工作" : ""}。`
    : `This execution ended; the overall goal was not complete at that time${event.campaign_continues === true ? " and further work remained" : ""}.`;
}

/** One plain sentence for the top of the map: how much is done and what is happening now. */
export function mapStatusSentence(input: {
  total: number;
  qa?: number;
  complete: number;
  ended?: number;
  reviewUnavailable?: number;
  held?: number;
  running: number;
  pending: boolean;
  paused: boolean;
  hasOpenWork: boolean;
  role?: string;
  /** A recorded action from the current task, already in the display language. */
  current?: string;
  /** The one sentence for a task waiting on its background team; wins over the role. */
  waiting?: string;
  zh: boolean;
}): string {
  const { total, qa = 0, complete, ended = 0, reviewUnavailable = 0, held = 0, running, pending, paused, hasOpenWork, role, current, waiting, zh } = input;
  const allDone = total > 0 && complete === total && ended === 0;
  const allDoneLabel = qa === total && qa > 0 ? (zh ? '已全部回答' : 'all answered') : (zh ? '已全部完成' : 'all completed');
  const counts = zh
    ? [
      total > qa ? `${total - qa} 个任务` : '',
      qa > 0 ? `${qa} 条问答` : '',
      complete > 0 && !allDone ? `已完成 ${complete} 个` : "",
      ended > 0 ? `${ended} 个任务结束时目标还没完成` : "",
      reviewUnavailable > 0 ? `${reviewUnavailable} 个结果尚未检查成功` : "",
      held > 0 ? `${held} 个任务暂停，后续工作尚未安排` : "",
      running > 1 ? `${running} 个任务同时进行` : "",
    ]
    : [
      total > qa ? `${total - qa} ${total - qa === 1 ? "task" : "tasks"}` : '',
      qa > 0 ? `${qa} Q&A` : '',
      complete > 0 && !allDone ? `${complete} done` : "",
      ended > 0 ? `${ended} ended before their goals were complete` : "",
      reviewUnavailable > 0 ? `${reviewUnavailable} ${reviewUnavailable === 1 ? "result has" : "results have"} not been checked successfully` : "",
      held > 0 ? `${held} paused with no next step scheduled` : "",
      running > 1 ? `${running} tasks in progress` : "",
    ];
  const state = pending
    ? zh ? "正在处理你的消息" : "working on your message"
    : waiting
      ? waiting
    : paused
      ? allDone
        ? allDoneLabel
        : hasOpenWork
          ? zh ? "已暂停" : "paused"
          : total > 0
            ? zh ? "没有在进行的工作" : "nothing in progress"
            : zh ? "就绪" : "ready"
      : current
        ? current
      : role
        ? roleActionLabel(role, zh)
        : allDone
          ? allDoneLabel
          : "";
  return [...counts, state]
    .filter(Boolean).join(" · ");
}
