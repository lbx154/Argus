import type { Snapshot, Role } from '../api';
import type { MissionView } from '../../../core/src/types';
import { theme } from '../lib/theme';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faPause, faPlay } from '@fortawesome/free-solid-svg-icons';
import { DaemonSpendBadge } from './DaemonSpendBadge';
import { useI18n } from '../i18n';

export type ThemeMode = 'light' | 'dark';

const ACTIVE_STATUSES = new Set(['running', 'in_progress', 'claimed']);

function currentRole(roles: Role[]): Role | undefined {
  return roles.find((role) => role.active) ?? roles.find((role) => role.role === 'manager');
}

export function TopBar({
  snap,
  streamOk,
  onStart,
  onStop,
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
  onStop: () => void;
  onManage: () => void;
  onOpenSessions?: () => void;
  mobileView?: 'activity' | 'preview';
  onToggleMobileView?: () => void;
  busy: boolean;
  snapshotStale?: boolean;
  readOnly?: boolean;
  missionView?: MissionView | null;
}) {
  const { t } = useI18n();
  const role = currentRole(snap.roles);
  const missionTerminal = ['complete', 'completed', 'done', 'success'].includes(
    String(missionView?.mission.status || '').toLowerCase(),
  );
  const missionRole = snap.daemon.alive && !missionTerminal
    ? missionView?.roles.find((candidate) => candidate.role === missionView.active_role)
    : undefined;
  const roleName = missionRole?.role || role?.role || 'manager';
  const roleActive = snap.daemon.alive && (
    missionRole ? missionRole.status === 'active' : Boolean(role?.active)
  );
  const activeItem = snap.backlog.find((item) => ACTIVE_STATUSES.has(item.status));
  const focus = missionRole?.label
    || activeItem?.title
    || activeItem?.objective
    || (missionTerminal ? missionView?.mission.summary || missionView?.mission.title : '')
    || snap.session.objective
    || t('common.ready');
  const degraded = Boolean(snap.partial || snap.observability?.slo.status === 'degraded');
  const externalDaemon = snap.daemon.alive && snap.daemon.control_available === false;
  const daemonActionLabel = externalDaemon
    ? t('topbar.externallyManaged')
    : snap.daemon.alive
    ? t('topbar.pauseDaemon')
    : t('topbar.runDaemon');
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
    <header className="chrome-seam-surface glass-panel glass-panel--raised flex h-12 min-w-0 shrink-0 items-center gap-2 border-b px-3 sm:gap-3 sm:px-4">
      {onOpenSessions ? (
        <button type="button" onClick={onOpenSessions} aria-label={t('topbar.openSessions')} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-faint hover:bg-bg hover:text-ink lg:hidden">
          <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.25">
            <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
          </svg>
        </button>
      ) : null}
      <div className="hidden min-w-0 max-w-28 truncate text-sm font-semibold text-ink sm:block">
        {snap.session.display_name || snap.session.id}
      </div>
      <span className="hidden h-4 w-px shrink-0 bg-line/40 sm:block" />
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span
          aria-label={roleActive ? t('topbar.roleActive', { role: roleName }) : t('topbar.roleIdle', { role: roleName })}
          className={`h-2 w-2 shrink-0 rounded-full ${roleActive ? 'animate-pulse' : ''}`}
          style={{ background: theme.role[roleName] || 'rgb(var(--ink-faint))' }}
        />
        <span className="hidden shrink-0 text-xs font-semibold capitalize text-ink-dim sm:inline">{roleName}</span>
        <span className="truncate text-xs text-ink-faint">{focus}</span>
      </div>
      <span
        title={healthTitle}
        className={`h-2 w-2 shrink-0 rounded-full transition-shadow duration-150 ${
          degraded || snapshotStale
            ? 'bg-err ring-1 ring-err/30 ring-offset-1 ring-offset-panel'
            : streamOk
            ? 'bg-ok ring-1 ring-ok/30 ring-offset-1 ring-offset-panel'
            : 'bg-ink-faint/50'
        }`}
      >
        <span className="sr-only">{healthTitle}</span>
      </span>
      <DaemonSpendBadge
        settledUsd={snap.spend_usd}
        knownUsd={snap.usage_summary?.known_cost_usd}
        status={snap.spend_status}
        calls={snap.usage_summary?.call_count}
        premiumRequests={snap.usage_summary?.premium_requests}
        live={snap.daemon.alive}
        compact
      />
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
          <button
            type="button"
            disabled={busy || externalDaemon}
            onClick={snap.daemon.alive ? onStop : onStart}
            aria-label={daemonActionLabel}
            title={externalDaemon ? t('topbar.externalDaemonHint') : daemonActionLabel}
            className="compact-control flex h-8 shrink-0 items-center gap-1 px-2 disabled:opacity-40"
          >
            <FontAwesomeIcon icon={snap.daemon.alive ? faPause : faPlay} className="h-3 w-3" />
            <span className="hidden sm:inline">{externalDaemon ? t('common.external') : snap.daemon.alive ? t('common.pause') : t('common.run')}</span>
          </button>
          <button
            type="button"
            aria-label={t('topbar.manageSession')}
            title={t('topbar.manageSession')}
            onClick={onManage}
            className="icon-control flex h-8 w-8 shrink-0 items-center justify-center text-sm tracking-widest"
          >
            ···
          </button>
        </>
      ) : null}
    </header>
  );
}
