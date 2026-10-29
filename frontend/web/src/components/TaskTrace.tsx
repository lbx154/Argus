import { useQuery } from '@tanstack/react-query';
import { api } from '../api';
import { ErrorMessage } from './ErrorMessage';

/** One thing a role did in a model call, as the runner recorded it. */
export type TraceEntry =
  | { kind: 'reasoning'; text: string; ts?: number }
  | { kind: 'message'; text: string; ts?: number }
  | { kind: 'command'; command: string; output: string; exit_code?: number; status?: string; ts?: number }
  | { kind: 'file_change'; paths: Array<{ path: string; change?: string }>; ts?: number }
  | { kind: 'tool'; tool: string; status?: string; error?: string; query?: string; ts?: number };

export interface TraceGroup {
  call_id: string;
  role: string;
  round?: number;
  label?: string;
  model?: string;
  started_ts?: number;
  ended_ts?: number;
  entries: TraceEntry[];
  truncated?: boolean;
}

export interface TaskTraceData {
  task_id: string;
  conclusion?: string;
  groups: TraceGroup[];
  truncated?: boolean;
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

function groupTitle(group: TraceGroup, zh: boolean): string {
  const role = ROLE[group.role]?.[zh ? 0 : 1] ?? group.role;
  if (!group.round) return role;
  return zh ? `${role} · 第 ${group.round} 轮` : `${role} · round ${group.round}`;
}

type Run = { commands: Extract<TraceEntry, { kind: 'command' }>[] } | { entry: Exclude<TraceEntry, { kind: 'command' }> };

/** Consecutive commands fold into one line: they are how the work was done,
 * not what it found, and stay one click away. */
function runs(entries: TraceEntry[]): Run[] {
  const out: Run[] = [];
  for (const entry of entries) {
    const last = out[out.length - 1];
    if (entry.kind === 'command') {
      if (last && 'commands' in last) last.commands.push(entry);
      else out.push({ commands: [entry] });
    } else out.push({ entry });
  }
  return out;
}

function EntryView({ entry, zh }: { entry: Exclude<TraceEntry, { kind: 'command' }>; zh: boolean }) {
  if (entry.kind === 'reasoning')
    return <p className="task-trace-reasoning whitespace-pre-wrap text-xs italic text-ink-dim">
      <span className="not-italic font-medium">{zh ? '思考 · ' : 'Thinking · '}</span>{entry.text.replace(/\*\*/g, '')}
    </p>;
  if (entry.kind === 'message')
    return <p className="task-trace-message whitespace-pre-wrap text-sm">{entry.text}</p>;
  if (entry.kind === 'file_change')
    return <p className="task-trace-files text-xs text-ink-dim">
      {zh ? '改动文件：' : 'Changed: '}{entry.paths.map((p) => p.path).join('、')}
    </p>;
  return <div className="task-trace-tool text-xs text-ink-dim">
    {zh ? '调用工具：' : 'Tool: '}{entry.tool}{entry.query ? ` · ${entry.query}` : ''}
    {entry.error || entry.status === 'failed' || entry.status === 'error'
      ? <ErrorMessage error={entry.error || entry.status} locale={zh ? 'zh-CN' : 'en'} />
      : entry.status && entry.status !== 'completed' ? <span>{zh ? ' · 调用未完成' : ' · Tool call incomplete'}</span> : null}
  </div>;
}

function CommandsView({ commands, zh }: { commands: Extract<TraceEntry, { kind: 'command' }>[]; zh: boolean }) {
  const failed = commands.filter((c) => typeof c.exit_code === 'number' && c.exit_code !== 0).length;
  return <details className="task-trace-commands text-xs text-ink-dim">
    <summary>{zh ? `运行了 ${commands.length} 条命令` : `Ran ${commands.length} command${commands.length === 1 ? '' : 's'}`}
      {failed ? (zh ? ` · ${failed} 条失败` : ` · ${failed} failed`) : ''}</summary>
    {commands.map((c, index) => <div key={index} className="mt-1">
      <code className="block whitespace-pre-wrap break-all">{c.command}</code>
      {c.output ? <pre className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap break-all opacity-80">{c.output}</pre> : null}
    </div>)}
  </details>;
}

/** What each role thought, said, ran and changed, by role and round. Reused
 * wherever a task's process is shown; the conclusion line comes first. */
export function TaskTrace({ sessionId, taskId, zh, conclusion, running = false }: {
  sessionId?: string; taskId: string; zh: boolean;
  /** Shown when the task has not delivered a result of its own yet. */
  conclusion?: string; running?: boolean;
}) {
  const trace = useTaskTrace(sessionId, taskId, running);
  const line = trace.data?.conclusion || conclusion;
  return <div className="task-trace" data-task-trace={taskId}>
    {line ? <p className="task-trace-conclusion mb-3 font-medium">{line}</p> : null}
    {trace.isLoading ? <p className="text-xs text-ink-dim">{zh ? '正在读取过程记录…' : 'Reading the record…'}</p>
      : trace.isError ? <p className="text-xs text-ink-dim">{zh ? '过程记录暂时读不到。' : 'The record could not be read.'}</p>
      : !trace.data?.groups.length ? <p className="text-xs text-ink-dim">{zh ? '还没有过程记录。' : 'No recorded process yet.'}</p>
      : trace.data.groups.map((group) => <section key={group.call_id} className="task-trace-group mb-3">
        <h4 className="mb-1 text-xs font-semibold text-ink-dim">{groupTitle(group, zh)}</h4>
        <div className="flex flex-col gap-1.5">
          {runs(group.entries).map((run, index) => 'commands' in run
            ? <CommandsView key={index} commands={run.commands} zh={zh} />
            : <EntryView key={index} entry={run.entry} zh={zh} />)}
          {group.truncated ? <p className="text-xs text-ink-dim">{zh ? '……之后的记录已省略' : '… later records omitted'}</p> : null}
        </div>
      </section>)}
  </div>;
}
