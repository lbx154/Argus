import { useQuery } from '@tanstack/react-query';
import { api, type Experiment } from '../api';
import { useI18n } from '../i18n';
import { EXPERIMENTS_CAPABILITY, RESOURCES_CAPABILITY, useCapability } from '../lib/capabilities';
import './experimentRuns.css';

/** How often the machine is read again: watching costs no model call, only a look at the machine. */
const EVERY_MS = 5_000;
/** A log this long without a write is said so; whether that is a problem is the team's call. */
const QUIET_SECONDS = 600;

/** Done and total from a log's last lines: tqdm's `12/400 [`, or `step|epoch|iter 12/400`. */
export function progressOf(lines: readonly string[]): { done: number; total: number } | null {
  for (const line of [...lines].reverse()) {
    const match = /(\d+)\/(\d+) \[/.exec(line) ?? /\b(?:step|steps|epoch|iter|iteration|batch)\b\s*[:=]?\s*\[?(\d+)\s*\/\s*(\d+)/i.exec(line);
    if (!match) continue;
    const [done, total] = [Number(match[1]), Number(match[2])];
    if (total > 0 && done <= total) return { done, total };
  }
  return null;
}

function duration(seconds: number, zh: boolean): string {
  if (seconds < 60) return zh ? `${seconds} 秒` : `${seconds}s`;
  if (seconds < 3600) return zh ? `${Math.floor(seconds / 60)} 分钟` : `${Math.floor(seconds / 60)} min`;
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
  return zh ? `${hours} 小时 ${minutes} 分` : `${hours} h ${minutes} min`;
}

/** The program and its arguments, without the interpreter's or the project's path. */
function shortCommand(command: string): string {
  return command.split(' ').map((part, index) => index === 0 ? part.split('/').pop() ?? part : part).join(' ');
}

/** What is running in the project, its GPUs and its log's end, read from the machine every few seconds. */
export function ExperimentRuns({ sid }: { sid: string }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const offered = useCapability(EXPERIMENTS_CAPABILITY);
  const hasResources = useCapability(RESOURCES_CAPABILITY);
  const runs = useQuery({
    queryKey: ['experiments', sid], queryFn: ({ signal }) => api.experiments(sid, signal),
    enabled: offered, refetchInterval: EVERY_MS, retry: false,
  });
  const experiments = runs.data?.experiments ?? [];
  const usesGpu = experiments.some(run => run.gpus.length);
  const resources = useQuery({
    queryKey: ['experiment-gpus', sid], queryFn: ({ signal }) => api.resources(sid, signal),
    enabled: offered && hasResources && usesGpu, refetchInterval: EVERY_MS, retry: false,
  });
  if (!experiments.length) return null;
  const devices = new Map((resources.data?.accelerators ?? []).flatMap(accelerator => accelerator.devices ?? []).map(device => [device.index, device]));
  return <section className="experiment-runs" data-testid="experiment-runs" aria-label={zh ? '正在运行的实验' : 'Running experiments'}>
    {experiments.map(run => <ExperimentRow key={run.pid} run={run} devices={devices} zh={zh} />)}
  </section>;
}

function ExperimentRow({ run, devices, zh }: { run: Experiment; devices: Map<string, { utilization_percent: number; used_memory_mib: number; total_memory_mib: number }>; zh: boolean }) {
  const progress = progressOf(run.tail);
  const last = run.tail.at(-1);
  const quiet = run.log_age_seconds !== null && run.log_age_seconds >= QUIET_SECONDS;
  return <article className="experiment-run">
    <header>
      <code title={run.command}>{shortCommand(run.command)}</code>
      <span>{zh ? `已运行 ${duration(run.seconds, zh)}` : `running ${duration(run.seconds, zh)}`}</span>
    </header>
    {run.gpus.length ? <ul className="experiment-gpus">
      {run.gpus.map(index => {
        const device = devices.get(index);
        return <li key={index}>GPU {index}{device ? ` · ${device.utilization_percent}% · ${(device.used_memory_mib / 1024).toFixed(1)}/${(device.total_memory_mib / 1024).toFixed(0)} GB` : ''}</li>;
      })}
    </ul> : null}
    {progress ? <div className="experiment-progress" role="progressbar" aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.done}>
      <span style={{ width: `${100 * progress.done / progress.total}%` }} />
      <small>{progress.done}/{progress.total} · {Math.floor(100 * progress.done / progress.total)}%</small>
    </div> : null}
    {run.log ? <details>
      <summary>
        <span className="experiment-last">{last ?? (zh ? '日志还是空的' : 'The log is empty')}</span>
        <span className={quiet ? 'experiment-quiet' : undefined}>
          {run.log_age_seconds === null ? '' : quiet
            ? zh ? `日志 ${duration(run.log_age_seconds, zh)}没有更新` : `log quiet for ${duration(run.log_age_seconds, zh)}`
            : run.log_age_seconds < 5 ? zh ? '刚刚更新' : 'written just now'
              : zh ? `${duration(run.log_age_seconds, zh)}前更新` : `written ${duration(run.log_age_seconds, zh)} ago`}
        </span>
      </summary>
      <p className="experiment-log-path">{run.log}</p>
      <pre>{run.tail.join('\n')}</pre>
    </details> : null}
  </article>;
}
