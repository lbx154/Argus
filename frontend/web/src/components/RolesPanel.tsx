import type { Role } from '../api';
import { theme, effortColor } from '../lib/theme';
import { PanelHeader } from './primitives';

const ORDER = ['manager', 'planner', 'engineer', 'reviewer'];

/** age_s (seconds since the role's last event) → "now"/"Ns"/"Nm"/"Nh". */
function ageLabel(age: number | null): string {
  if (age == null) return '';
  if (age < 3) return 'now';
  if (age < 60) return `${Math.floor(age)}s`;
  if (age < 3600) return `${Math.floor(age / 60)}m`;
  return `${Math.floor(age / 3600)}h`;
}

/**
 * Compact role ledger. Colour is confined to a small state marker; the panel
 * remains readable as operational data instead of four glowing cards.
 */
export function RolesPanel({ roles }: { roles: Role[] }) {
  const byRole = new Map(roles.map((r) => [r.role, r]));
  const ordered = ORDER.map((r) => byRole.get(r)).filter(Boolean) as Role[];
  const extra = roles.filter((r) => !ORDER.includes(r.role));
  const all = [...ordered, ...extra];

  return (
    <section className="card">
      <PanelHeader title="Roles" />
      <div>
        {all.map((r) => {
          const hue = theme.role[r.role] ?? theme.info;
          return (
            <div key={r.role} className="grid grid-cols-[84px_minmax(0,1fr)_auto] items-center gap-2 border-b border-line/60 px-3 py-2 last:border-b-0">
              <div className="flex items-center gap-1.5">
                <span
                  className="inline-block h-1.5 w-1.5 rounded-full"
                  style={{ background: r.active ? hue : '#55564f' }}
                />
                <span
                  className="text-[11px] font-medium capitalize"
                  style={{ color: r.active ? hue : theme.inkDim }}
                >
                  {r.role}
                </span>
              </div>
              <div className="min-w-0 truncate font-mono text-[10px] text-ink-faint" title={r.model}>{r.model || '—'}</div>
              <div className="flex items-center gap-1 text-right">
                <span className="text-[10px]" style={{ color: r.active ? theme.ink : theme.inkFaint }}>{r.active ? r.status || 'active' : 'idle'}</span>
                {r.active && ageLabel(r.age_s) && (
                  <span className="text-[10px] tabular-nums text-ink-faint">· {ageLabel(r.age_s)}</span>
                )}
                {r.effort && (
                  <span className="text-[10px]" style={{ color: effortColor(r.effort) }}>
                    · {r.effort}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
