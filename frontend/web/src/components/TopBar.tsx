import { WorkspaceHeader } from './WorkspaceShell';
import { SessionWorkdir } from './SessionWorkdir';
import type { Snapshot, EventMsg } from '../api';
import type { MissionView } from '../../../core/src/types';
import { roleLabel } from '../lib/enumLabels';
import { PHASE_TONE, teamPhase, teamPhaseLabel } from './TeamControls';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faPause, faPlay, faStop, faTerminal } from '@fortawesome/free-solid-svg-icons';
import { useI18n } from '../i18n';
import { currentWorkStartedAt, currentWorkStatus } from '../lib/workStatus';
import { workSummary } from '../lib/workSummary';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';
import { isFreshProject } from '../lib/workStatus';
import { resumableTaskIds } from '../lib/resumable';
import { shortcut } from '../lib/format';

export type ThemeMode = 'light' | 'dark';

const ACTIVE_STATUSES = new Set(['running', 'in_progress', 'claimed']);

/** Status = one small dot + words; colour lives only on the dot. */

export function TopBar({
  snap,
  streamOk,
  onStart,
  onStop,
  onPause,
  onManage,
  onOpenPalette,
  onOpenSessions,
  mobileView,
  onToggleMobileView,
  busy,
  snapshotStale = false,
  readOnly = false,
  missionView,
  events = [],
}: {
  snap: Snapshot;
  streamOk: boolean;
  onStart: () => void;
  /** Immediate interruption; asks the operator to confirm first. */
  onStop: () => void;
  /** Pause once the running task finishes; falls back to onStop when absent. */
  onPause?: () => void;
  onManage: () => void;
  /** Opens the command palette; shown here on narrow screens, where the workspace tabs are hidden. */
  onOpenPalette?: () => void;
  onOpenSessions?: () => void;
  mobileView?: 'activity' | 'preview';
  onToggleMobileView?: () => void;
  busy: boolean;
  snapshotStale?: boolean;
  readOnly?: boolean;
  missionView?: MissionView | null;
  events?: EventMsg[];
}) {
  const { t, locale } = useI18n();
  const work = currentWorkStatus(snap, missionView, events);
  const progress = workSummary(work, missionView, events, locale, streamOk,
    currentWorkStartedAt(snap, missionView, work.taskId));
  const missionTerminal = ['complete', 'completed', 'done', 'success'].includes(
    String(missionView?.mission.status || '').toLowerCase(),
  );
  const roleName = work.role || 'manager';
  const roleActive = work.state === 'running' && streamOk && !snapshotStale;
  const activeItem = snap.backlog.find((item) => ACTIVE_STATUSES.has(item.status));
  const focus = relativizeWorkspacePaths(work.title
    || activeItem?.title
    || activeItem?.objective
    || (missionTerminal ? missionView?.mission.summary || missionView?.mission.title : '')
    || snap.session.objective
    || '', snap.session.workdir, snap.session.cwd);
  const degraded = Boolean(snap.partial || snap.observability?.slo.status === 'degraded');
  const phase = teamPhase(work, snap);
  // A project nobody has written to yet has finished nothing: it waits for the person (rule 6).
  const untouched = isFreshProject(snap) && phase === 'done' && !events.length && !focus;
  const statusWords = !streamOk ? t('common.reconnecting') : snapshotStale ? t('common.stale')
    : untouched ? (locale === 'zh-CN' ? '等你开始' : 'Waiting for you to start') : progress || teamPhaseLabel(phase, locale);
  const externalDaemon = snap.daemon.alive && snap.daemon.control_available === false;
  // A foreground request (a direct answer or a one-agent task) runs without
  // the daemon; offering "Run" then reads as if nothing had started.
  const foregroundWork = !snap.daemon.alive && ['running', 'waiting'].includes(currentWorkStatus(snap, missionView, events).state);
  const hasQueuedWork = snap.backlog.some(item => ['pending', 'queued', 'running', 'in_progress', 'claimed', 'paused'].includes(item.status));
  const draining = snap.daemon.alive && snap.daemon.draining === true;
  // Work paused or cut off with nothing running it: going on is the one thing
  // to do here, so it is the header's primary action, not a hover control.
  const resumable = !foregroundWork && resumableTaskIds(snap).size > 0;
  const daemonActionLabel = externalDaemon
    ? t('topbar.externallyManaged')
    : draining
    ? t('topbar.draining')
    : snap.daemon.alive
    ? onPause ? t('topbar.pauseTeam') : t('topbar.pauseDaemon')
    : foregroundWork
    ? t('topbar.working')
    : hasQueuedWork ? t('topbar.resumeTeam') : t('topbar.runDaemon');
  const stopNow = () => {
    if (window.confirm(t('daemon.stopConfirm'))) onStop();
  };
  const healthTitle = degraded
    ? [
        ...(snap.diagnostics ?? []).map((item) => `${item.section}: ${item.message}`),
        ...(snap.observability?.slo.violations ?? []),
      ].join('\n') || t('common.degraded')
    : snapshotStale
    ? t('common.stale')
    : streamOk
    ? t('common.live')
    : t('common.reconnecting');

  return (
    <WorkspaceHeader>
      {onOpenSessions ? (
        <button type="button" onClick={onOpenSessions} aria-label={t('topbar.openSessions')} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-faint hover:bg-bg hover:text-ink lg:hidden">
          <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
            <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
          </svg>
        </button>
      ) : null}
      <div className="topbar-heading flex min-w-0 flex-1 flex-col justify-center gap-0.5">
        <div className="topbar-title min-w-0 truncate font-serif text-[19px] font-medium leading-6 tracking-[-0.005em] text-ink" title={snap.session.display_name || snap.session.id}>
          {snap.session.display_name || t('sidebar.unnamedSession')}
        </div>
        <div className="topbar-status flex min-w-0 items-center gap-2 text-xs text-ink-faint">
          <span
            className="status-dot"
            data-tone={!streamOk || snapshotStale || untouched ? 'idle' : PHASE_TONE[phase]}
            data-role-dot={roleActive ? roleName : undefined}
            aria-label={roleActive ? t('topbar.roleActive', { role: roleLabel(roleName, t) }) : undefined}
            aria-hidden={roleActive ? undefined : true}
          />
          <span className="min-w-0 truncate text-ink-dim" title={focus ? `${statusWords} · ${focus}` : statusWords}>{statusWords}</span>
          <SessionWorkdir session={snap.session} />
        </div>
      </div>
      {degraded || snapshotStale || !streamOk ? <span
        title={healthTitle}
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${degraded || snapshotStale ? 'bg-err' : 'bg-silver'}`}
      >
        <span className="sr-only">{healthTitle}</span>
      </span> : <span className="sr-only">{healthTitle}</span>}
      {onToggleMobileView ? (
        <button
          type="button"
          onClick={onToggleMobileView}
          aria-label={mobileView === 'activity' ? t('topbar.showPreview') : t('topbar.showActivity')}
          title={mobileView === 'activity' ? t('topbar.showPreview') : t('topbar.showActivity')}
          className="icon-control flex h-8 w-8 shrink-0 items-center justify-center lg:hidden"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
            {mobileView === 'activity'
              ? <><rect x="2" y="2.5" width="12" height="11" rx="1.5" /><path d="M9.5 2.75v10.5" /></>
              : <path d="M3 4h10M3 8h10M3 12h7" />}
          </svg>
        </button>
      ) : null}
      {!readOnly ? (
        <>
          {resumable ? <button
            type="button"
            data-resume-work
            disabled={busy}
            onClick={onStart}
            title={t('topbar.continueRunHint')}
            className="brand-button brand-button-primary flex h-8 shrink-0 items-center gap-1.5 px-3 disabled:opacity-40"
          >
            <FontAwesomeIcon icon={faPlay} className="h-3 w-3" />
            <span>{t('topbar.continueRun')}</span>
          </button> : (snap.daemon.alive || foregroundWork || hasQueuedWork) && <button
            type="button"
            disabled={busy || externalDaemon || foregroundWork || draining}
            onClick={snap.daemon.alive ? (onPause ?? stopNow) : onStart}
            aria-label={daemonActionLabel}
            title={externalDaemon ? t('topbar.externalDaemonHint') : snap.daemon.alive && onPause && !draining ? t('topbar.pauseAfterCurrentHint') : daemonActionLabel}
            className="compact-control flex h-8 shrink-0 items-center gap-1 px-2 disabled:opacity-40"
          >
            <FontAwesomeIcon icon={snap.daemon.alive ? faPause : faPlay} className="h-3 w-3" />
            <span className="hidden sm:inline">{externalDaemon ? t('common.external') : draining ? t('topbar.draining') : snap.daemon.alive ? t('topbar.pauseTeam') : foregroundWork ? t('common.working') : hasQueuedWork ? t('topbar.resumeTeam') : t('common.run')}</span>
          </button>}
          {snap.daemon.alive && onPause && !externalDaemon ? <button
            type="button"
            disabled={busy}
            onClick={stopNow}
            aria-label={t('topbar.stopNow')}
            title={t('topbar.stopNowHint')}
            className="compact-control flex h-8 shrink-0 items-center gap-1 px-2 text-err disabled:opacity-40"
          >
            <FontAwesomeIcon icon={faStop} className="h-3 w-3" />
            <span className="hidden md:inline">{t('topbar.stopNow')}</span>
          </button> : null}
          {onOpenPalette ? <button
            type="button"
            onClick={onOpenPalette}
            aria-label={t('topbar.commands')}
            title={t('topbar.commandsHint', { keys: shortcut('K') })}
            className="compact-control flex h-8 shrink-0 items-center gap-1 px-2 lg:hidden"
          >
            <FontAwesomeIcon icon={faTerminal} className="h-3 w-3" />
            <span>{t('topbar.commands')}</span>
          </button> : null}
          <button
            type="button"
            aria-label={t('topbar.manageSession')}
            title={t('topbar.manageSession')}
            onClick={onManage}
            className="icon-control flex h-8 w-8 shrink-0 items-center justify-center text-base leading-none tracking-[0.12em] text-ink-dim"
          >
            ···
          </button>
        </>
      ) : null}
    </WorkspaceHeader>
  );
}
