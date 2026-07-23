import { useEffect, useState } from 'react';
import type { GitDiffView, MissionView } from '../../../core/src/types';
import {
  displayObjective,
  formatMissionElapsed,
} from '../../../core/src/missionView';
import { outcomeDimensionSummary } from '../../../core/src/missionOutcome';
import { formatBytes } from '../lib/format';
import { theme } from '../lib/theme';
import { MarkdownContent } from './MarkdownContent';

const ROLE_ORDER = ['manager', 'planner', 'engineer', 'reviewer'];

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

export function compactMissionDag(view: MissionView, limit = 16) {
  const ordered = orderedDag(view);
  if (ordered.length <= limit) return { nodes: ordered, hidden: [] as typeof ordered };
  const keep = new Set(ordered.slice(-limit).map((node) => node.id));
  const active = ordered.find((node) => ['running', 'in_progress', 'claimed'].includes(node.status));
  const byId = new Map(ordered.map((node) => [node.id, node]));
  const stack = active ? [active] : [];
  while (stack.length) {
    const node = stack.pop()!;
    if (keep.has(node.id)) continue;
    keep.add(node.id);
    node.deps.forEach((dep) => {
      const parent = byId.get(dep);
      if (parent) stack.push(parent);
    });
  }
  return {
    nodes: ordered.filter((node) => keep.has(node.id)),
    hidden: ordered.filter((node) => !keep.has(node.id)),
  };
}

function Achievement({ view }: { view: MissionView }) {
  const achievement = view.achievement;
  if (!achievement) return null;
  return (
    <section className="border-b border-ok/35 bg-ok/5 px-5 py-4 animate-appear">
      <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ok">Argus achievement</div>
      <div className="mt-2 text-sm font-semibold text-ink">{achievement.title}</div>
      {achievement.summary ? <div className="mt-1 text-xs text-ink-dim">{achievement.summary}</div> : null}
      <div className="mt-2 text-xs"><span className="text-ink-faint">Elapsed </span><span className="font-mono text-ink">{formatMissionElapsed(achievement.elapsed_seconds ?? 0)}</span></div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-ink-dim">
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
  const roleMap = new Map(view.roles.map((role) => [role.role, role]));
  const activeNode = view.dag.find((node) => ['running', 'in_progress', 'claimed'].includes(node.status));
  const dagView = compactMissionDag(view);
  const dag = dagView.nodes;
  const objective = displayObjective(
    view.mission.objective || view.mission.title || 'Waiting for a mission',
  );
  const [replayIndex, setReplayIndex] = useState(Math.max(0, view.timeline.length - 1));
  const [selectedRole, setSelectedRole] = useState(view.active_role || 'planner');
  const [selectedTaskId, setSelectedTaskId] = useState(activeNode?.id || '');
  const outcome = outcomeDimensionSummary(view.outcome);
  useEffect(() => setReplayIndex(Math.max(0, view.timeline.length - 1)), [view.timeline.length]);
  useEffect(() => {
    if (activeNode?.id) setSelectedTaskId(activeNode.id);
  }, [activeNode?.id]);
  const replayRows = view.timeline.slice(0, replayIndex + 1).slice(-12).reverse();
  const selectedTask = view.dag.find((node) => node.id === selectedTaskId);
  const selectedRoleWork = view.role_work
    .filter((item) => item.role === selectedRole)
    .filter((item) => !selectedTaskId || !item.item_id || item.item_id === selectedTaskId)
    .slice(-40)
    .reverse();
  return (
    <section className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto bg-panel scroll-thin" aria-label="Mission control">
      <header className="border-b border-line/60 px-5 py-5">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">Mission</div>
        <div
          role="heading"
          aria-level={1}
          className="mt-1 line-clamp-4 max-w-4xl text-lg font-semibold leading-snug text-ink"
          title={objective}
        >
          <MarkdownContent>{objective}</MarkdownContent>
        </div>
        {objective.length > 600 ? (
          <details className="mt-2 text-xs text-ink-faint">
            <summary className="cursor-pointer hover:text-ink">Show full objective</summary>
            <div className="mt-2 text-ink-dim"><MarkdownContent>{objective}</MarkdownContent></div>
          </details>
        ) : null}
        <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-3">
          <div><div className="text-ink-faint">Stage</div><div className="mt-0.5 font-medium capitalize text-blue-sky">{view.stage.label || view.stage.id || '—'}</div></div>
          <div><div className="text-ink-faint">Campaign</div><div className="mt-0.5 font-mono text-ink">{formatMissionElapsed(view.mission.campaign_elapsed_seconds)}</div></div>
          <div><div className="text-ink-faint">Round</div><div className="mt-0.5 font-mono text-ink">{view.round.current || '—'}{view.round.max ? ` / ${view.round.max}` : ''}</div></div>
        </div>
        {outcome.length ? (
          <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-ink-dim">
            {outcome.map((row) => <span key={row}>{row}</span>)}
          </div>
        ) : null}
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
              <button
                key={name}
                type="button"
                onClick={() => setSelectedRole(name)}
                className={`min-w-0 border-l-2 pl-3 text-left ${selectedRole === name ? 'bg-white/[0.03]' : ''}`}
                style={{ borderColor: active || role?.status === 'done' ? color : 'rgb(var(--line))' }}
              >
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${active ? 'animate-pulse motion-reduce:animate-none' : ''}`} style={{ background: rejected ? theme.error : active || role?.status === 'done' ? color : theme.inkFaint }} />
                  <span className="text-xs font-semibold capitalize" style={{ color }}>{name}</span>
                </div>
                <div className={`mt-1 truncate text-xs ${rejected ? 'text-err' : 'text-ink-dim'}`}>{role?.label || 'Waiting'}</div>
              </button>
            );
          })}
        </div>
      </section>

      <section className="border-b border-line/60 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
            Role work · <span className="text-blue-sky">{selectedRole}</span>
          </div>
          {selectedTask ? (
            <button type="button" onClick={() => setSelectedTaskId('')} className="text-[10px] text-ink-faint hover:text-ink">
              filtered by {selectedTask.title || selectedTask.id} · clear
            </button>
          ) : <span className="text-[10px] text-ink-faint">all visible missions</span>}
        </div>
        <div className="mt-3 grid gap-2 lg:grid-cols-2">
          {selectedRoleWork.map((item) => (
            <article key={item.id} className="min-w-0 rounded border border-line/60 bg-bg/35 px-3 py-2">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate text-xs font-medium text-ink">{item.title}</span>
                <time className="shrink-0 font-mono text-[10px] text-ink-faint">
                  {new Date(item.ts * 1000).toISOString().slice(11, 19)}
                </time>
              </div>
              <div className="mt-1 flex gap-2 font-mono text-[10px] text-ink-faint">
                <span>{item.kind}</span>
                {item.status ? <span>{item.status}</span> : null}
                {item.round_index != null ? <span>round {item.round_index}</span> : null}
              </div>
              {item.detail ? <p className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-ink-dim scroll-thin">{item.detail}</p> : null}
            </article>
          ))}
          {!selectedRoleWork.length ? (
            <div className="col-span-full py-8 text-center text-xs text-ink-faint">
              No persisted {selectedRole} work for this selection yet.
            </div>
          ) : null}
        </div>
      </section>

      <div className="grid min-h-[320px] border-b border-line/60 lg:grid-cols-[minmax(0,1.15fr)_minmax(260px,0.85fr)]">
        <section className="min-w-0 border-b border-line/60 px-5 py-4 lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between">
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">Research DAG</div>
            {activeNode ? <span className="max-w-48 truncate text-[10px] text-blue-sky">active · {activeNode.title}</span> : null}
          </div>
          <div className="mt-3 space-y-0">
            {dagView.hidden.length ? (
              <div className="mb-3 rounded border border-line/60 bg-bg/50 px-3 py-2 text-[10px] text-ink-faint">
                {dagView.hidden.length} earlier tasks collapsed · {dagView.hidden.filter((node) => ['failed', 'blocked'].includes(node.status)).length} failed · {dagView.hidden.filter((node) => node.status === 'skipped').length} skipped
              </div>
            ) : null}
            {dag.length ? dag.map((node, index) => {
              const active = node.id === activeNode?.id;
              const done = ['done', 'completed'].includes(node.status);
              const failed = ['failed', 'blocked'].includes(node.status);
              return (
                <button
                  key={node.id}
                  type="button"
                  onClick={() => setSelectedTaskId(node.id)}
                  className={`relative flex w-full min-w-0 gap-3 pb-3 text-left last:pb-0 ${selectedTaskId === node.id ? 'bg-white/[0.03]' : ''}`}
                >
                  {index < dag.length - 1 ? <span className="absolute left-[5px] top-3 h-full w-px bg-line" /> : null}
                  <span className={`relative z-10 mt-1 h-3 w-3 shrink-0 rounded-full border-2 border-panel ${active ? 'animate-pulse bg-blue motion-reduce:animate-none' : done ? 'bg-ok' : failed ? 'bg-err' : 'bg-ink-faint'}`} />
                  <div className="min-w-0 flex-1">
                    <div className={`truncate text-xs font-medium ${active ? 'text-blue-sky' : 'text-ink'}`}>{node.title || node.objective || node.id}</div>
                    <div className="mt-0.5 flex gap-2 font-mono text-[10px] text-ink-faint"><span>{node.status}</span>{node.deps.length ? <span>after {node.deps.join(', ')}</span> : null}</div>
                  </div>
                </button>
              );
            }) : <div className="py-12 text-center text-xs text-ink-faint">Planner has not added DAG nodes yet.</div>}
          </div>
          {selectedTask ? (
            <div className="mt-3 rounded border border-blue/25 bg-blue/5 px-3 py-3">
              <div className="text-xs font-semibold text-blue-sky">{selectedTask.title || selectedTask.id}</div>
              {selectedTask.objective ? <p className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-ink-dim">{selectedTask.objective}</p> : null}
              {selectedTask.acceptance_check ? (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">Acceptance</div>
                  <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-ink-dim">{selectedTask.acceptance_check}</p>
                </div>
              ) : null}
              {selectedTask.non_goals?.length ? (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">Non-goals</div>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-ink-dim">
                    {selectedTask.non_goals.map((goal) => <li key={goal}>{goal}</li>)}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>

        <section className="min-w-0 px-5 py-4">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">Capabilities</div>
          {view.learned_skills.length ? (
            <div className="mt-3">
              <div className="text-[10px] uppercase tracking-[0.12em] text-ok">Capabilities unlocked</div>
              <div className="mt-2 space-y-2">
                {view.learned_skills.filter((skill) => skill.status === 'active').slice(-8).map((skill) => (
                  <details key={String(skill.id)} className="rounded border border-ok/35 bg-ok/5 px-2 py-1.5">
                    <summary className="cursor-pointer text-[10px] text-ok">{String(skill.name || skill.id)}</summary>
                    <div className="mt-2 space-y-1 font-mono text-[9px] text-ink-faint">
                      {skill.mission_title ? <div>evolved during · {skill.mission_title}</div> : null}
                      {skill.path ? <div className="break-all">path · {skill.path}</div> : null}
                      <div>version · {skill.version} · scope · {skill.scope || 'project'}</div>
                    </div>
                    {skill.content ? (
                      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap border-t border-ok/20 pt-2 font-mono text-[10px] leading-5 text-ink-dim scroll-thin">
                        {skill.content}{skill.content_truncated ? '\n… content truncated' : ''}
                      </pre>
                    ) : <div className="mt-2 text-[10px] text-ink-faint">Skill content is not available in this snapshot.</div>}
                  </details>
                ))}
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
          {(view.storage.project_skill_dir || view.storage.global_skill_dir || view.storage.wiki_paths.length || view.storage.skill_history_compressed || view.storage.wiki_retired_compressed) ? (
            <div className="mt-4 border-t border-line/50 pt-3">
              <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">Self-evolution storage</div>
              <div className="mt-2 space-y-1 font-mono text-[10px] text-ink-dim">
                {view.storage.project_skill_dir ? <div className="break-all">project skills ({view.storage.project_skill_count}) · {view.storage.project_skill_dir}</div> : null}
                {view.storage.global_skill_dir ? <div className="break-all">global skills ({view.storage.global_skill_count}) · {view.storage.global_skill_dir}</div> : null}
                {view.storage.wiki_paths.map((path) => <div key={path} className="break-all">project wiki · {path}</div>)}
                {(view.storage.skill_history_compressed || view.storage.wiki_retired_compressed) ? <div>cold history · skill {view.storage.skill_history_compressed} · wiki {view.storage.wiki_retired_compressed} · {formatBytes(view.storage.skill_history_bytes_saved + view.storage.wiki_retired_bytes_saved)} saved</div> : null}
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
