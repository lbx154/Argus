import { WorkspaceHeader } from './WorkspaceShell';
import { SessionWorkdir } from './SessionWorkdir';
import type { Snapshot } from '../api';
import type { MissionView } from '../../../core/src/types';
import { roleLabel } from '../lib/enumLabels';
import { PHASE_TONE, RunControl, teamPhase } from './TeamControls';
import { useI18n } from '../i18n';
import { currentWorkStatus, workStatusLabel } from '../lib/workStatus';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';
import { formatRelativeTime, money } from '../lib/format';
import { SPEND_ESTIMATE_HINT } from '../lib/projectOverview';

export type ThemeMode = 'light' | 'dark';

const ACTIVE_STATUSES = new Set(['running', 'in_progress', 'claimed']);

/** Status = one small dot + words; colour lives only on the dot. */

export function TopBar({
  snap,
  streamOk,
  onStart,
  onStop,
  pending = false,
  showRunControl = true,
  onManage,
  onOpenSessions,
  mobileView,
  onToggleMobileView,
  busy,
  snapshotStale = false,
  readOnly = false,
  missionView,
}: {
  snap: Snapshot;
  streamOk: boolean;
  onStart: () => void;
  /** Stops the work under way at once; the control says first what that interrupts. */
  onStop: () => void;
  /** A message sent is waiting for its reply. */
  pending?: boolean;
  /** The map already carries this control beside its team status. */
  showRunControl?: boolean;
  onManage: () => void;
  onOpenSessions?: () => void;
  mobileView?: 'activity' | 'preview';
  onToggleMobileView?: () => void;
  busy: boolean;
  snapshotStale?: boolean;
  readOnly?: boolean;
  missionView?: MissionView | null;
}) {
  const { t, locale } = useI18n();
  const work = currentWorkStatus(snap);
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
  const phase = teamPhase(work);
  const statusWords = workStatusLabel(work, locale);
  const activityAt = work.activityAt ?? snap.session.last_active;
  const spent = snap.spend_usd;
  const spendLabel = spent != null && Number.isFinite(spent) && snap.spend_status !== 'empty'
    ? t('topbar.spent', { amount: `${snap.spend_status === 'partial' ? '≥' : ''}${money(spent, spent < 10 ? 2 : 1)}` })
    : '';
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
    <WorkspaceHeader className="project-header">
      <div className="topbar-title min-w-0 truncate font-serif text-[19px] font-medium leading-6 tracking-[-0.005em] text-ink" title={snap.session.display_name || snap.session.id}>
        {snap.session.display_name || t('sidebar.unnamedSession')}
      </div>
      <div className="topbar-status min-w-0 items-center text-xs text-ink-faint">
        <span
          className="status-dot"
          data-tone={!streamOk || snapshotStale ? 'idle' : PHASE_TONE[phase]}
          data-role-dot={roleActive ? roleName : undefined}
          aria-label={roleActive ? t('topbar.roleActive', { role: roleLabel(roleName, t) }) : undefined}
          aria-hidden={roleActive ? undefined : true}
        />
        <span className="text-ink-dim" title={focus ? `${statusWords} · ${focus}` : statusWords}>{statusWords}</span>
        {activityAt ? <><span aria-hidden="true">·</span><time dateTime={new Date(activityAt * 1000).toISOString()}>{formatRelativeTime(activityAt, locale)}</time></> : null}
        {spendLabel ? <><span aria-hidden="true">·</span><span title={SPEND_ESTIMATE_HINT[locale === 'zh-CN' ? 'zh' : 'en']}>{spendLabel}</span></> : null}
        <div className="hidden sm:block"><SessionWorkdir session={snap.session} /></div>
      </div>
      <div className="topbar-actions flex shrink-0 items-center gap-2 sm:gap-3">
        {onOpenSessions ? (
          <button type="button" onClick={onOpenSessions} aria-label={t('topbar.openSessions')} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-faint hover:bg-bg hover:text-ink lg:hidden">
            <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
              <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
            </svg>
          </button>
        ) : null}
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
            {showRunControl && <RunControl snapshot={snap} pending={pending} busy={busy} onStop={onStop} onResume={onStart}
              className="compact-control flex h-8 shrink-0 items-center gap-1 px-2 text-err disabled:opacity-40"
              resumeClassName="brand-button brand-button-primary flex h-8 shrink-0 items-center gap-1.5 px-3 disabled:opacity-40" />}
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
      </div>
    </WorkspaceHeader>
  );
}
