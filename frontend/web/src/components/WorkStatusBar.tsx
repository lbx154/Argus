import { useEffect, useState } from 'react';
import type { EventMsg, MissionView, Snapshot } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { currentWorkStatus, workStatusLabel } from '../lib/workStatus';
import { TeamPauseButton, teamPhase, teamPhaseLabel } from './TeamControls';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';

/** Shared, factual status for the conversation and its progress panel. */
export function WorkStatusBar({ snapshot, view, events = [], connected, compact = false, onPauseTeam, onResumeTeam, teamBusy }: {
  snapshot?: Snapshot; view?: MissionView | null; events?: EventMsg[]; connected: boolean; compact?: boolean;
  onPauseTeam?: () => void; onResumeTeam?: () => void; teamBusy?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now() / 1000), 10_000); return () => window.clearInterval(timer); }, []);
  const raw = currentWorkStatus(snapshot, view, events, now);
  const status = { ...raw, title: relativizeWorkspacePaths(raw.title, snapshot?.session.workdir, snapshot?.session.cwd) };
  const phase = teamPhase(status, snapshot);
  // Compact (chat header) keeps one line; only a step that needs the person adds a hint.
  const actionable = ['launch_confirm', 'operator_input', 'task_failed'].includes(status.reason);
  const next = compact && !actionable ? '' : status.reason === 'launch_confirm'
    ? zh ? '核对上方卡片里的理解和计划，点“开始执行”后才会开始。' : 'Check the plan in the card above; work starts only when you press Start.'
    : status.reason === 'operator_input'
    ? zh ? '需要你的决定。查看下方问题，确认后继续。' : 'Your decision is needed. Review the question below to continue.'
    : status.reason === 'task_failed'
      ? zh ? '这次工作未完成。已有记录和文件仍保留，可在对话中调整要求或请求继续。' : 'This work did not finish. Existing records and files are retained; adjust the request or ask to continue in the conversation.'
      : status.state === 'paused'
        ? zh ? '当前没有继续执行。你可以调整要求，或在运行控制中查看恢复方式。' : 'Work is not continuing. Adjust the request or check runtime controls before resuming.'
        : status.state === 'step_finished'
          ? zh ? '可以查看文件与成果，或在下方提出新的要求。' : 'Review the files and results, or add your next request below.'
          : status.state === 'running'
            ? zh ? '你可以继续阅读记录或补充要求，不必停留在这个页面。' : 'You can read the history or add context while work continues.'
            : '';
  const seconds = status.activityAgeSeconds;
  const age = seconds === null ? (zh ? '暂无本次运行的进展记录' : 'No progress recorded for this run yet')
    : seconds < 60 ? (zh ? '刚刚有进展' : 'Progress in the last minute')
      : seconds < 3600 ? (zh ? `${Math.floor(seconds / 60)} 分钟前有进展` : `Progress ${Math.floor(seconds / 60)} min ago`)
        : (zh ? `${Math.floor(seconds / 3600)} 小时前有进展` : `Progress ${Math.floor(seconds / 3600)} hr ago`);
  return <div className="shrink-0 border-b border-line/60 bg-panel px-4 py-2.5 text-xs" data-testid="work-status" data-state={status.state}>
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${!connected ? 'bg-warn' : status.state === 'running' ? 'bg-blue animate-pulse motion-reduce:animate-none' : status.state === 'waiting' ? 'bg-warn' : 'bg-ink-faint'}`} aria-hidden="true" />
      <span className="font-semibold text-ink" data-testid="team-phase" data-phase={phase}>{teamPhaseLabel(phase, locale)}</span>
      <span className="min-w-0 truncate text-ink-dim" title={status.title || undefined}>{workStatusLabel(status, locale, connected)}{compact && status.title && phase === 'running' ? ` · ${status.title}` : ''}</span>
      {age ? <time className="text-ink-faint" dateTime={status.activityAt ? new Date(status.activityAt * 1000).toISOString() : undefined}>{age}</time> : null}
      {snapshot && (onPauseTeam || onResumeTeam) ? <TeamPauseButton snapshot={snapshot} locale={locale} onPause={onPauseTeam} onResume={onResumeTeam} busy={teamBusy}
        className="ml-auto rounded border border-line px-2 py-0.5 text-ink-dim hover:bg-bg hover:text-ink disabled:opacity-50" /> : null}
    </div>
    {!connected ? <p className="mt-1 leading-5 text-ink-faint">{zh ? '这里保留的是已收到的记录；连接恢复后继续更新。' : 'These are the records already received. Updates resume when the connection returns.'}</p>
      : !compact && status.title ? <p className="mt-1 line-clamp-2 leading-5 text-ink-dim" title={status.title}>{status.title}</p> : null}
    {connected && next && <p className="mt-1 leading-5 text-ink-faint">{next}</p>}
  </div>;
}
