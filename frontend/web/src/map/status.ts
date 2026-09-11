/** One plain sentence for the top of the map: how much is done and what is happening now. */
export function mapStatusSentence(input: {
  total: number;
  complete: number;
  running: number;
  pending: boolean;
  paused: boolean;
  hasOpenWork: boolean;
  role?: string;
  zh: boolean;
}): string {
  const { total, complete, running, pending, paused, hasOpenWork, role, zh } = input;
  const allDone = total > 0 && complete === total;
  const counts = zh
    ? [
      `${total} 个任务`,
      complete > 0 && !allDone ? `已完成 ${complete}` : "",
      running > 0 ? `进行中 ${running}` : "",
    ]
    : [
      `${total} ${total === 1 ? "task" : "tasks"}`,
      complete > 0 && !allDone ? `${complete} done` : "",
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
          : zh ? "就绪" : "ready"
      : roleName
        ? zh ? `${roleName} 正在工作` : `${roleName} is working`
        : allDone
          ? zh ? "已全部完成" : "all completed"
          : "";
  return [...counts, state].filter(Boolean).join(" · ");
}
