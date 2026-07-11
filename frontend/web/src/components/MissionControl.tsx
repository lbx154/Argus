import { useEffect, useState } from 'react';
import type { GitDiffView, MissionMetricView, MissionView } from '../../../core/src/types';
import {
  formatMissionElapsed,
  metricDisplay,
  missionMetricImprovement,
} from '../../../core/src/missionView';
import { theme } from '../lib/theme';

const ROLE_ORDER = ['manager', 'planner', 'engineer', 'reviewer'];

function metricSeries(view: MissionView): MissionMetricView[] {
  const name = view.primary_metric?.name;
  if (!name) return [];
  const metrics = view.metrics
    .filter((metric) => metric.name === name)
    .sort((left, right) => Number(left.reported_at ?? 0) - Number(right.reported_at ?? 0));
  const baseline = metrics[0]?.baseline;
  if (baseline == null) return metrics;
  return [{
    ...metrics[0],
    id: `${metrics[0].id}-baseline`,
    value: baseline,
    baseline,
    verification_status: 'accepted',
    reported_at: Number(metrics[0].reported_at ?? 0) - 1,
  }, ...metrics];
}

function orderedDag(view: MissionView) {
  const pending = [...view.dag];
  const ordered = [] as typeof view.dag;
  const emitted = new Set<string>();
  while (pending.length) {
    const index = pending.findIndex((node) => node.deps.every((dep) => emitted.has(dep) || !view.dag.some((candidate) => candidate.id === dep)));
    const [node] = pending.splice(index >= 0 ? index : 0, 1);
    ordered.push(node);
    emitted.add(node.id);
  }
  return ordered;
}

function MetricChart({ view }: { view: MissionView }) {
  const metrics = metricSeries(view);
  if (!metrics.length) {
    return <div className="flex h-36 items-center justify-center text-xs text-ink-faint">Waiting for reported metrics</div>;
  }
  const values = metrics.map((metric) => metric.value);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = Math.max(1e-9, high - low);
  const points = metrics.map((metric, index) => {
    const x = metrics.length === 1 ? 50 : 4 + (index / (metrics.length - 1)) * 92;
    const y = 32 - ((metric.value - low) / span) * 26;
    return `${x},${y}`;
  }).join(' ');
  return (
    <div className="min-h-36">
      <svg viewBox="0 0 100 38" role="img" aria-label={`${view.primary_metric?.name} metric history`} className="h-28 w-full overflow-visible">
        <path d="M4 32H96" stroke="rgb(var(--line))" strokeWidth="0.6" />
        <polyline points={points} fill="none" stroke="#78b892" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
        {metrics.map((metric, index) => {
          const [x, y] = points.split(' ')[index].split(',');
          const accepted = metric.verification_status === 'accepted';
          return <circle key={metric.id} cx={x} cy={y} r={accepted ? 2 : 1.4} fill={accepted ? '#78b892' : '#d1ad68'} />;
        })}
      </svg>
      <div className="flex items-center justify-between font-mono text-[10px] text-ink-faint">
        <span>{metrics[0].value}{metrics[0].unit}</span>
        <span>{metrics.length} report{metrics.length === 1 ? '' : 's'}</span>
        <span>{metrics[metrics.length - 1].value}{metrics[metrics.length - 1].unit}</span>
      </div>
    </div>
  );
}

function Achievement({ view }: { view: MissionView }) {
  const achievement = view.achievement;
  if (!achievement) return null;
  return (
    <section className="border-b border-ok/35 bg-ok/5 px-5 py-4 animate-appear">
      <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ok">Argus achievement</div>
      <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
        <div><div className="text-ink-faint">Baseline</div><div className="mt-0.5 font-mono text-ink">{achievement.baseline ?? '—'}{achievement.unit}</div></div>
        <div><div className="text-ink-faint">Best</div><div className="mt-0.5 font-mono text-ok">{achievement.best ?? '—'}{achievement.unit}</div></div>
        <div><div className="text-ink-faint">Gain</div><div className="mt-0.5 font-mono text-ok">{achievement.gain == null ? '—' : `${achievement.gain >= 0 ? '+' : ''}${achievement.gain.toFixed(2)}`}{achievement.unit}</div></div>
        <div><div className="text-ink-faint">Elapsed</div><div className="mt-0.5 font-mono text-ink">{formatMissionElapsed(achievement.elapsed_seconds ?? 0)}</div></div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-ink-dim">
        <span>{achievement.experiments_run ?? 0} experiments</span>
        <span>{achievement.rejected_attempts ?? 0} rejected attempts</span>
        <span>{achievement.skills_learned ?? 0} skills learned</span>
        <span>{achievement.artifacts ?? 0} artifacts</span>
      </div>
    </section>
  );
}

export function MissionControl({
  view,
  onOpenArtifact,
  gitDiff,
}: {
  view: MissionView;
  onOpenArtifact?: (path: string) => void;
  gitDiff?: GitDiffView;
}) {
  const metric = view.primary_metric;
  const improvement = missionMetricImprovement(metric);
  const roleMap = new Map(view.roles.map((role) => [role.role, role]));
  const activeNode = view.dag.find((node) => ['running', 'in_progress', 'claimed'].includes(node.status));
  const dag = orderedDag(view);
  const [replayIndex, setReplayIndex] = useState(Math.max(0, view.timeline.length - 1));
  useEffect(() => setReplayIndex(Math.max(0, view.timeline.length - 1)), [view.timeline.length]);
  const replayRows = view.timeline.slice(0, replayIndex + 1).slice(-12).reverse();
  return (
    <section className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto bg-panel scroll-thin" aria-label="Mission control">
      <header className="border-b border-line/60 px-5 py-5">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">Mission</div>
        <h1 className="mt-1 max-w-4xl text-lg font-semibold leading-snug text-ink">
          {view.mission.objective || view.mission.title || 'Waiting for a mission'}
        </h1>
        <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-4">
          <div><div className="text-ink-faint">Stage</div><div className="mt-0.5 font-medium capitalize text-blue-sky">{view.stage.label || view.stage.id || '—'}</div></div>
          <div><div className="text-ink-faint">Elapsed</div><div className="mt-0.5 font-mono text-ink">{formatMissionElapsed(view.mission.elapsed_seconds)}</div></div>
          <div><div className="text-ink-faint">Round</div><div className="mt-0.5 font-mono text-ink">{view.round.current || '—'}{view.round.max ? ` / ${view.round.max}` : ''}</div></div>
          <div><div className="text-ink-faint">Best</div><div className={`mt-0.5 font-mono ${metric?.verification_status === 'accepted' ? 'text-ok' : 'text-warn'}`}>{metricDisplay(metric)}{improvement == null ? '' : ` · ${improvement >= 0 ? '↑' : '↓'}${Math.abs(improvement).toFixed(2)}${metric?.unit || ''}`}</div></div>
        </div>
      </header>

      <Achievement view={view} />

      <section className="border-b border-line/60 px-5 py-4">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">AI research team</div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {ROLE_ORDER.map((name) => {
            const role = roleMap.get(name);
            const active = role?.status === 'active';
            const rejected = role?.status === 'rejected' || role?.status === 'error';
            const color = theme.role[name] ?? theme.inkFaint;
            return (
              <div key={name} className="min-w-0 border-l-2 pl-3" style={{ borderColor: active || role?.status === 'done' ? color : 'rgb(var(--line))' }}>
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${active ? 'animate-pulse motion-reduce:animate-none' : ''}`} style={{ background: rejected ? theme.error : active || role?.status === 'done' ? color : theme.inkFaint }} />
                  <span className="text-xs font-semibold capitalize" style={{ color }}>{name}</span>
                </div>
                <div className={`mt-1 truncate text-xs ${rejected ? 'text-err' : 'text-ink-dim'}`}>{role?.label || 'Waiting'}</div>
              </div>
            );
          })}
        </div>
      </section>

      <div className="grid min-h-[320px] border-b border-line/60 lg:grid-cols-[minmax(0,1.15fr)_minmax(260px,0.85fr)]">
        <section className="min-w-0 border-b border-line/60 px-5 py-4 lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between">
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">Research DAG</div>
            {activeNode ? <span className="max-w-48 truncate text-[10px] text-blue-sky">active · {activeNode.title}</span> : null}
          </div>
          <div className="mt-3 space-y-0">
            {dag.length ? dag.map((node, index) => {
              const active = node.id === activeNode?.id;
              const done = ['done', 'completed'].includes(node.status);
              const failed = ['failed', 'blocked'].includes(node.status);
              return (
                <div key={node.id} className="relative flex min-w-0 gap-3 pb-3 last:pb-0">
                  {index < dag.length - 1 ? <span className="absolute left-[5px] top-3 h-full w-px bg-line" /> : null}
                  <span className={`relative z-10 mt-1 h-3 w-3 shrink-0 rounded-full border-2 border-panel ${active ? 'animate-pulse bg-blue motion-reduce:animate-none' : done ? 'bg-ok' : failed ? 'bg-err' : 'bg-ink-faint'}`} />
                  <div className="min-w-0 flex-1">
                    <div className={`truncate text-xs font-medium ${active ? 'text-blue-sky' : 'text-ink'}`}>{node.title || node.objective || node.id}</div>
                    <div className="mt-0.5 flex gap-2 font-mono text-[10px] text-ink-faint"><span>{node.status}</span>{node.deps.length ? <span>after {node.deps.join(', ')}</span> : null}</div>
                  </div>
                </div>
              );
            }) : <div className="py-12 text-center text-xs text-ink-faint">Planner has not added DAG nodes yet.</div>}
          </div>
        </section>

        <section className="min-w-0 px-5 py-4">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">Metric history</div>
          <div className="mt-2"><MetricChart view={view} /></div>
          {view.learned_skills.length ? (
            <div className="mt-4 border-t border-line/50 pt-3">
              <div className="text-[10px] uppercase tracking-[0.12em] text-ok">Capabilities unlocked</div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {view.learned_skills.filter((skill) => skill.status === 'active').slice(-6).map((skill) => <span key={String(skill.id)} className="rounded border border-ok/35 bg-ok/5 px-2 py-1 text-[10px] text-ok">{String(skill.name || skill.id)}</span>)}
              </div>
            </div>
          ) : null}
          {view.learned_wiki_pages.some((page) => page.status !== 'retired') ? (
            <div className="mt-4 border-t border-line/50 pt-3">
              <div className="text-[10px] uppercase tracking-[0.12em] text-blue-sky">Knowledge retained</div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {view.learned_wiki_pages.filter((page) => page.status !== 'retired').slice(-6).map((page) => <span key={String(page.id)} className="rounded border border-blue/35 bg-blue/5 px-2 py-1 text-[10px] text-blue-sky">{String(page.title || page.id)}</span>)}
              </div>
            </div>
          ) : null}
          {(view.storage.project_skill_dir || view.storage.global_skill_dir || view.storage.wiki_paths.length) ? (
            <div className="mt-4 border-t border-line/50 pt-3">
              <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">Self-evolution storage</div>
              <div className="mt-2 space-y-1 font-mono text-[10px] text-ink-dim">
                {view.storage.project_skill_dir ? <div className="break-all">project skills ({view.storage.project_skill_count}) · {view.storage.project_skill_dir}</div> : null}
                {view.storage.global_skill_dir ? <div className="break-all">global skills ({view.storage.global_skill_count}) · {view.storage.global_skill_dir}</div> : null}
                {view.storage.wiki_paths.map((path) => <div key={path} className="break-all">project wiki · {path}</div>)}
              </div>
            </div>
          ) : null}
        </section>
      </div>

      <section className="px-5 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">Mission replay</div>
          {view.timeline.length > 1 ? (
            <>
              <input
                type="range"
                min={0}
                max={view.timeline.length - 1}
                value={replayIndex}
                onChange={(event) => setReplayIndex(Number(event.target.value))}
                aria-label="Replay mission timeline"
                className="h-1 min-w-32 flex-1 accent-blue"
              />
              <span className="font-mono text-[10px] text-ink-faint">{replayIndex + 1}/{view.timeline.length}</span>
            </>
          ) : null}
        </div>
        <div className="mt-3 space-y-3">
          {replayRows.map((item) => (
            <div key={item.id} className="grid grid-cols-[44px_10px_minmax(0,1fr)] gap-2 text-xs">
              <time className="font-mono text-[10px] text-ink-faint">{new Date(item.ts * 1000).toISOString().slice(11, 16)}</time>
              <span className={`mt-1 h-2 w-2 rounded-full ${item.tone === 'error' ? 'bg-err' : item.tone === 'success' || item.tone === 'metric' || item.tone === 'skill' ? 'bg-ok' : 'bg-blue'}`} />
              <div className="min-w-0"><span className="font-medium text-ink">{item.title}</span>{item.detail ? <span className="text-ink-dim"> · {item.detail}</span> : null}</div>
            </div>
          ))}
          {!view.timeline.length ? <div className="py-10 text-center text-xs text-ink-faint">Waiting for structured research events.</div> : null}
        </div>
        {view.artifacts.length ? (
          <div className="mt-5 flex flex-wrap gap-2 border-t border-line/50 pt-4">
            {view.artifacts.slice(-8).map((artifact) => {
              const path = String(artifact.path || '');
              return (
                <button key={String(artifact.id || path)} type="button" disabled={!path || !onOpenArtifact} onClick={() => path && onOpenArtifact?.(path)} className="rounded border border-line px-2 py-1 font-mono text-[10px] text-blue-sky hover:border-blue-sky/50 disabled:text-ink-faint">
                  {String(artifact.title || path)}
                </button>
              );
            })}
          </div>
        ) : null}
        {gitDiff?.available && (gitDiff.status || gitDiff.diff) ? (
          <details className="mt-5 border-t border-line/50 pt-4">
            <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint hover:text-ink">
              Git changes{gitDiff.branch ? ` · ${gitDiff.branch}` : ''}
            </summary>
            {gitDiff.stat ? <pre className="mt-3 overflow-x-auto whitespace-pre-wrap font-mono text-[10px] leading-5 text-ink-dim">{gitDiff.stat}</pre> : null}
            {gitDiff.diff ? <pre className="mt-3 max-h-80 overflow-auto whitespace-pre font-mono text-[10px] leading-5 text-ink-dim scroll-thin">{gitDiff.diff}{gitDiff.truncated ? '\n… diff truncated' : ''}</pre> : null}
          </details>
        ) : null}
      </section>
    </section>
  );
}
