import type { MapEvent } from "./model";

export function completionScope(event: MapEvent | undefined, zh: boolean): string {
  if (event?.type !== "life.mission.completed" ||
    !(event.overall_complete === false || event.campaign_continues === true)) return "";
  return zh
    ? `本次执行已结束；总体目标尚未完成${event.campaign_continues === true ? "，仍需后续工作" : ""}。`
    : `This execution ended; the overall goal is not complete${event.campaign_continues === true ? " and further work remains" : ""}.`;
}

/** One plain sentence for the top of the map: how much is done and what is happening now. */
export function mapStatusSentence(input: {
  total: number;
  complete: number;
  ended?: number;
  running: number;
  pending: boolean;
  paused: boolean;
  hasOpenWork: boolean;
  role?: string;
  zh: boolean;
}): string {
  const { total, complete, ended = 0, running, pending, paused, hasOpenWork, role, zh } = input;
  const allDone = total > 0 && complete === total && ended === 0;
  const counts = zh
    ? [
      `${total} 个任务`,
      complete > 0 && !allDone ? `已完成 ${complete}` : "",
      ended > 0 ? `${ended} 次执行已结束` : "",
      running > 0 ? `进行中 ${running}` : "",
    ]
    : [
      `${total} ${total === 1 ? "task" : "tasks"}`,
      complete > 0 && !allDone ? `${complete} done` : "",
      ended > 0 ? `${ended} ${ended === 1 ? "execution" : "executions"} ended` : "",
      running > 0 ? `${running} running` : "",
    ];
  const roleName = role
    ? ({ planner: "Planner", manager: "Manager", engineer: "Engineer", reviewer: "Reviewer" } as Record<string, string>)[role] || role
    : "";
  const state = pending
    ? zh ? "正在处理你的消息" : "working on your message"
    : paused
      ? allDone
        ? zh ? "已全部完成" : "all completed"
        : hasOpenWork
          ? zh ? "已暂停" : "paused"
          : total > 0
            ? zh ? "没有在进行的工作" : "nothing in progress"
            : zh ? "就绪" : "ready"
      : roleName
        ? zh ? `${roleName} 正在工作` : `${roleName} is working`
        : allDone
          ? zh ? "已全部完成" : "all completed"
          : "";
  return [...counts, state, ended > 0 ? zh ? "执行结束不代表总体目标完成" : "execution completion is not overall completion" : ""]
    .filter(Boolean).join(" · ");
}
