import { useEffect, useState } from 'react';
import type { Snapshot } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { currentWorkStatus, workStatusLabel } from '../lib/workStatus';
import { PHASE_TONE, RunControl, teamPhase } from './TeamControls';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';
import { roleActionLabel } from '../lib/taskLanguage';

/** Shared, factual status for the conversation and its progress panel. */
export function WorkStatusBar({ snapshot, connected, compact = false, pending, onStopWork, onResumeTeam, teamBusy, managerMessage }: {
  snapshot?: Snapshot; connected: boolean; compact?: boolean;
  /** A message sent is waiting for its reply. */
  pending?: boolean; onStopWork?: () => void; onResumeTeam?: () => void; teamBusy?: boolean;
  managerMessage?: string;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now() / 1000), 10_000); return () => window.clearInterval(timer); }, []);
  const raw = currentWorkStatus(snapshot, now);
  const status = { ...raw, title: relativizeWorkspacePaths(raw.title, snapshot?.session.workdir, snapshot?.session.cwd) };
  const phase = teamPhase(status);
  // Compact (chat header) keeps one line; only a step that needs the person adds a hint.
  const actionable = status.reason === 'operator_input';
  const next = compact && !actionable ? '' : actionable
    ? zh ? '需要你的决定。查看下方问题，确认后继续。' : 'Your decision is needed. Review the question below to continue.'
    : status.state === 'paused'
      ? zh ? '当前没有继续执行。你可以调整要求，或在运行控制中查看恢复方式。' : 'Work is not continuing. Adjust the request or check runtime controls before resuming.'
      : status.state === 'idle' && status.activityAt !== null
        ? zh ? '可以查看文件与成果，或在下方提出新的要求。' : 'Review the files and results, or add your next request below.'
        : '';
  // How long since the last progress matters while the work runs.
  const seconds = status.activityAgeSeconds;
  const age = phase !== 'running' ? '' : seconds === null ? (zh ? '暂无本次运行的进展记录' : 'No progress recorded for this run yet')
    : seconds < 60 ? (zh ? '刚刚有进展' : 'Progress in the last minute')
      : seconds < 3600 ? (zh ? `${Math.floor(seconds / 60)} 分钟前有进展` : `Progress ${Math.floor(seconds / 60)} min ago`)
        : (zh ? `${Math.floor(seconds / 3600)} 小时前有进展` : `Progress ${Math.floor(seconds / 3600)} hr ago`);
  const label = status.state === 'running' && status.role === 'manager'
    ? roleActionLabel('manager', zh, '', managerMessage)
    : workStatusLabel(status, locale);
  const tone = !connected ? 'idle' : PHASE_TONE[phase];
  return <div className="work-status shrink-0 px-4 py-2 text-xs sm:px-5" data-testid="work-status" data-state={status.state}>
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="status-dot" data-tone={tone} aria-hidden="true" />
      <span className="min-w-0 truncate font-medium text-ink" data-testid="team-phase" data-phase={phase} title={status.title || undefined}>
        {label}
      </span>
      {age ? <time className="text-ink-faint" dateTime={status.activityAt ? new Date(status.activityAt * 1000).toISOString() : undefined}>{age}</time> : null}
      {snapshot && onStopWork && onResumeTeam ? <RunControl snapshot={snapshot} pending={pending} onStop={onStopWork} onResume={onResumeTeam} busy={teamBusy}
        className="ml-auto flex items-center gap-1 rounded-md border border-line px-2 py-0.5 text-ink-dim hover:bg-sunken hover:text-ink disabled:opacity-50" /> : null}
    </div>
    {!connected ? <p className="mt-1 pl-[15px] leading-5 text-ink-faint">{zh ? '连接恢复后继续更新。' : 'Updates resume when the connection returns.'}</p>
      : !compact && status.title ? <p className="mt-1 line-clamp-2 pl-[15px] leading-5 text-ink-dim" title={status.title}>{status.title}</p> : null}
    {connected && next && <p className="mt-1 pl-[15px] leading-5 text-ink-faint">{next}</p>}
  </div>;
}
