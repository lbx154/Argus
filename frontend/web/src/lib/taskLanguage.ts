/** User-facing actions; role identifiers belong in the work details. */
export function shortWorkText(text: string, max = 120): string {
  const line = text.replace(/\s+/g, ' ').trim();
  return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
}

export function roleActionLabel(role: string | undefined, zh: boolean, title = ''): string {
  const task = shortWorkText(title, 70);
  switch (role) {
    case 'manager': return zh ? '正在整理任务进展' : 'Summarizing progress';
    case 'planner': return zh ? '正在安排接下来要做的事' : 'Planning what to do next';
    case 'reviewer': return task
      ? zh ? `正在检查结果：${task}` : `Checking the result: ${task}`
      : zh ? '正在检查结果是否正确' : 'Checking whether the result is correct';
    default: return task
      ? zh ? `正在处理：${task}` : `Working on: ${task}`
      : zh ? '正在处理当前任务' : 'Working on the current task';
  }
}
