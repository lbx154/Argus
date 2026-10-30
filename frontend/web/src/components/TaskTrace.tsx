import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';

/** The letters the server recorded, grouped by the call that wrote them. */
export interface TraceGroup {
  call_id: string;
  role: string;
  started_ts?: number;
  ended_ts?: number;
  entries: { kind: 'message'; text: string }[];
}

export interface TaskTraceData {
  task_id: string;
  groups: TraceGroup[];
}

const ROLE: Record<string, [string, string]> = {
  planner: ['规划', 'Planner'],
  engineer: ['执行', 'Engineer'],
  reviewer: ['审查', 'Reviewer'],
  manager: ['Argus', 'Argus'],
};

/** The recorded trace of one task, read once and refreshed while it runs. */
export function useTaskTrace(sessionId: string | undefined, taskId: string | undefined, running = false) {
  return useQuery({
    queryKey: ['task-trace', sessionId, taskId],
    queryFn: ({ signal }) => api.mapTrace(sessionId!, taskId!, signal),
    enabled: !!sessionId && !!taskId,
    staleTime: running ? 0 : 60_000,
    refetchInterval: running ? 10_000 : false,
  });
}

/** A task's recorded letters, with its current words before they reach the record. */
export function TaskTrace({ sessionId, taskId, zh, conclusion, running = false, workspace }: {
  sessionId?: string; taskId: string; zh: boolean;
  /** Shown when the task has not delivered a result of its own yet. */
  conclusion?: string; running?: boolean; workspace?: string;
}) {
  const trace = useTaskTrace(sessionId, taskId, running);
  const line = conclusion;
  return <div className="task-trace" data-task-trace={taskId}>
    {line && (trace.isError || !trace.data?.groups.some(group => group.entries.some(entry => entry.text === line)))
      ? <p className="task-trace-conclusion mb-3 font-medium">{relativizeWorkspacePaths(line, workspace)}</p> : null}
    {trace.isLoading ? <p className="text-xs text-ink-dim">{zh ? '正在读取过程记录…' : 'Reading the record…'}</p>
      : trace.isError ? <p className="text-xs text-ink-dim">{zh ? '过程记录暂时读不到。' : 'The record could not be read.'}</p>
      : !trace.data?.groups.length ? <p className="text-xs text-ink-dim">{zh ? '还没有过程记录。' : 'No recorded process yet.'}</p>
      : trace.data.groups.map((group) => <section key={group.call_id} className="task-trace-group mb-3">
        <h3 className="mb-1 text-xs font-semibold text-ink-dim">{ROLE[group.role]?.[zh ? 0 : 1] ?? group.role}</h3>
        <div className="flex flex-col gap-1.5">
          {group.entries.map((entry, index) => <p key={index} className="task-trace-message whitespace-pre-wrap text-sm">{relativizeWorkspacePaths(entry.text, workspace)}</p>)}
        </div>
      </section>)}
  </div>;
}
