import type { Snapshot, Role } from '../api';
import type { Spend } from '../lib/cost';
import { CostGauge } from './CostGauge';
import { Button, Chip, StatusDot } from './primitives';
import { effortColor } from '../lib/theme';
import { uptime } from '../lib/format';
import { deriveMissionView, type MissionState } from '../../../core/src/mission';
import type { ContinuousState } from '../../../core/src/types';

const STATE_COLOR: Record<MissionState, string> = {
  working: '#8fa7b8',
  waiting: '#c1a363',
  complete: '#7fa386',
  idle: '#7e7d75',
  offline: '#c77b72',
};

/** Pick the role whose backend/model best represents "what's running now". */
function primaryRole(roles: Role[]): Role | undefined {
  return roles.find((r) => r.active) ?? roles.find((r) => r.role === 'manager') ?? roles[0];
}

/**
 * Top bar: project identity, daemon health, the backend/model/effort summary
 * (the same trio the terminal footer shows), the cost gauge, and daemon start/
 * stop. `streamOk` reflects the live WS connection.
 */
export function TopBar({
  snap,
  spend,
  streamOk,
  onStart,
  onStop,
  busy,
  busyLabel,
  snapshotStale = false,
  readOnly = false,
  continuous,
  onToggleContinuous,
  continuousBusy = false,
}: {
  snap: Snapshot;
  spend: Spend;
  streamOk: boolean;
  onStart: () => void;
  onStop: () => void;
  busy: boolean;
  busyLabel?: string;
  snapshotStale?: boolean;
  readOnly?: boolean;
  continuous?: ContinuousState;
  onToggleContinuous?: () => void;
  continuousBusy?: boolean;
}) {
  const d = snap.daemon;
  const pr = primaryRole(snap.roles);
  const backend = pr?.backend_label || d.backend || '—';
  const model = pr?.model || '—';
  const effort = pr?.effort ?? null;
  const mission = deriveMissionView(snap, continuous);

  return (
    <header className="flex min-h-[58px] items-center gap-2 border-b border-line bg-surface py-2.5 pl-14 pr-3 md:gap-4 md:px-5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <StatusDot ok={d.alive} title={d.alive ? 'daemon alive' : 'stopped'} />
          <h1 className="truncate text-sm font-semibold text-ink">
            {snap.session.display_name || snap.session.id}
          </h1>
          <Chip color={STATE_COLOR[mission.state]}>{mission.stateLabel}</Chip>
          <span className="hidden text-[11px] text-ink-faint lg:inline">
            {d.alive ? `daemon up ${uptime(d.uptime_seconds)}` : 'daemon off'} · {streamOk ? '● live' : '○ reconnecting'}
            {snapshotStale ? <span className="text-warn"> · snapshot stale</span> : null}
            {snap.partial ? (
              <span
                className="text-err"
                title={(snap.diagnostics ?? []).map((item) => `${item.section}: ${item.message}`).join('\n')}
              >
                {' · snapshot partial'}
              </span>
            ) : null}
          </span>
        </div>
        <p className="mt-0.5 truncate text-xs text-ink-faint">
          {mission.objective || 'No active mission'}
        </p>
      </div>

      {/* continuous self-directed campaign */}
      <div className="hidden lg:contents">{continuous &&
        (readOnly ? (
          continuous.enabled && <Chip color="#c7a66a">campaign live</Chip>
        ) : (
          <button
            onClick={onToggleContinuous}
            disabled={continuousBusy}
            title={continuous.enabled ? 'stop the self-directed campaign' : 'start a self-directed campaign'}
            className={`chip transition-colors disabled:cursor-wait disabled:opacity-50 ${
              continuous.enabled ? 'text-gold' : 'text-ink-faint hover:text-ink-dim'
            }`}
            style={continuous.enabled ? { borderColor: '#c7a66a55' } : undefined}
          >
            {continuousBusy ? 'updating…' : continuous.enabled ? '● campaign' : '○ campaign'}
          </button>
        ))}</div>

      {/* backend / model / effort — the terminal footer trio */}
      <div className="hidden items-center gap-2 2xl:flex">
        <Chip color="#8fa7b8">{backend}</Chip>
        <Chip>{model}</Chip>
        {effort && (
          <Chip color={effortColor(effort)}>effort {effort}</Chip>
        )}
      </div>

      <div className="hidden sm:block">
        <CostGauge
          spend={spend}
          settledUsd={snap.spend_usd}
          spendStatus={snap.spend_status}
          daemon={d}
          backendLabel={backend}
          requestUsage={snap.request_usage}
        />
      </div>

      <div className="flex items-center gap-1.5">
        {readOnly ? (
          <Chip color="#c7a66a">kiosk</Chip>
        ) : d.alive ? (
          <Button variant="danger" onClick={onStop} disabled={busy} title="stop the daemon (drain)">
            {busy ? busyLabel || 'working…' : 'stop'}
          </Button>
        ) : (
          <Button variant="primary" onClick={onStart} disabled={busy} title="start the daemon">
            {busy ? busyLabel || 'working…' : 'start'}
          </Button>
        )}
      </div>
    </header>
  );
}
