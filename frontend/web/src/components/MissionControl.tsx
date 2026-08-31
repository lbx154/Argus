import { useEffect, useRef, useState } from 'react';
import type { DeliveryReceipt, GitDiffView, MissionView } from '../../../core/src/types';
import {
  displayObjective,
  formatMissionElapsed,
} from '../../../core/src/missionView';
import { theme } from '../lib/theme';
import { formatRelativeTime } from '../lib/format';
import { MarkdownContent } from './MarkdownContent';
import { useI18n } from '../i18n';
import { api, type ArtifactInfo, type Snapshot } from '../api';
import {
  outcomeLabels,
  roleLabel,
  statusLabel,
} from '../lib/enumLabels';

const ROLE_ORDER = ['manager', 'planner', 'engineer', 'reviewer'];
const ACTIVE_WORK_STATUSES = ['active', 'running', 'in_progress', 'claimed'];
const TERMINAL_MISSION_STATUSES = ['complete', 'completed', 'done', 'success', 'incomplete', 'stalled', 'blocked', 'ended'];
const MILLISECONDS_PER_DAY = 86_400_000;

function missionRoleLabel(role: string, t: (key: string) => string) {
  return ROLE_ORDER.includes(role) ? t(`role.${role}`) : roleLabel(role, t);
}

export function formatMissionEventTime(ts: number, locale: string, now = new Date()) {
  const date = new Date(ts * 1000);
  const time = date.toLocaleTimeString(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  });
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const eventDay = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  if (eventDay === today) return time;

  const firstWeekday = locale === 'zh-CN' ? 1 : 0;
  const daysSinceWeekStart = (now.getDay() - firstWeekday + 7) % 7;
  const weekStart = today - daysSinceWeekStart * MILLISECONDS_PER_DAY;
  if (eventDay >= weekStart && eventDay < today) {
    const weekday = date.toLocaleDateString(locale, { weekday: 'short' });
    return `${weekday} ${time}`;
  }

  const calendarDate = date.toLocaleDateString(locale, {
    month: 'short',
    day: 'numeric',
  });
  return `${calendarDate} ${time}`;
}

function RoleWorkDetail({ detail }: { detail: string }) {
  const { t } = useI18n();
  const detailRef = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [canExpand, setCanExpand] = useState(false);

  useEffect(() => {
    if (expanded) return;
    const element = detailRef.current;
    if (!element) return;
    const measure = () => setCanExpand(element.scrollHeight > element.clientHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [detail, expanded]);

  return (
    <div className="mt-2">
      <p
        ref={detailRef}
        className={`${expanded ? '' : 'line-clamp-3'} whitespace-pre-wrap break-words text-[11px] leading-5 text-ink-dim`}
      >
        {detail}
      </p>
      {canExpand ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          className="mt-1 text-[11px] text-blue-sky hover:text-ink"
        >
          {t(expanded ? 'mission.showLess' : 'mission.showMore')}
        </button>
      ) : null}
    </div>
  );
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
  const { t } = useI18n();
  const achievement = view.achievement;
  if (!achievement) return null;
  return (
    <section className="border-b border-ok/35 bg-ok/5 px-5 py-4 animate-appear">
      <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ok">{t('mission.achievement')}</div>
      <div className="mt-2 text-sm font-semibold text-ink">{achievement.title}</div>
      {achievement.summary ? <div className="mt-1 text-xs text-ink-dim">{achievement.summary}</div> : null}
      <div className="mt-2 text-xs"><span className="text-ink-faint">{t('mission.elapsed')} </span><span className="font-mono text-ink">{formatMissionElapsed(achievement.elapsed_seconds ?? 0)}</span></div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-ink-dim">
        <span>{t('mission.rejectedAttempts', { count: achievement.rejected_attempts ?? 0 })}</span>
        <span>{t('mission.skillsLearned', { count: achievement.skills_learned ?? 0 })}</span>
        <span>{t('mission.artifacts', { count: achievement.artifacts ?? 0 })}</span>
      </div>
    </section>
  );
}

export function MissionControl({
  view,
  sid = '',
  snapshot,
  artifacts = [],
  onOpenArtifact,
  onOpenDelivery,
  gitDiff,
}: {
  view: MissionView;
  sid?: string;
  snapshot?: Snapshot;
  artifacts?: ArtifactInfo[];
  onOpenArtifact?: (path: string) => void;
  onOpenDelivery?: (delivery: DeliveryReceipt) => void;
  gitDiff?: GitDiffView;
}) {
  const { locale, t } = useI18n();
  const roleMap = new Map(view.roles.map((role) => [role.role, role]));
  const activeNode = view.dag.find((node) => ['running', 'in_progress', 'claimed'].includes(node.status));
  const dagView = compactMissionDag(view);
  const dag = dagView.nodes;
  const objective = displayObjective(
    view.mission.objective || view.mission.title || t('mission.waiting'),
  );
  const [replayIndex, setReplayIndex] = useState(Math.max(0, view.timeline.length - 1));
  const [selectedRole, setSelectedRole] = useState(view.active_role || 'planner');
  const [selectedTaskId, setSelectedTaskId] = useState(activeNode?.id || '');
  const [resumeBusy, setResumeBusy] = useState(false);
  const delivery = view.delivery;
  const artifactByPath = new Map(artifacts.map((artifact) => [artifact.path, artifact]));
  const healthNeedsAttention = ['degraded', 'red', 'critical'].includes(view.health?.toLowerCase() ?? '');
  const missionFailed = ['failed', 'error'].includes(view.mission.status.toLowerCase());
  const stepFailed = view.dag.some((node) => node.status.toLowerCase() === 'failed');
  const missionPaused = ['hold', 'paused'].includes(view.stage.id.toLowerCase());
  const needsAttention = healthNeedsAttention || missionFailed || stepFailed || missionPaused;
  const attentionKey = healthNeedsAttention
    ? 'mission.attentionHealth'
    : missionFailed
      ? 'mission.attentionFailed'
        : stepFailed
          ? 'mission.attentionStepFailed'
          : 'mission.attentionPaused';
  const activeWork = view.role_work
    .filter((item) => ACTIVE_WORK_STATUSES.includes(item.status.toLowerCase()))
    .sort((left, right) => right.ts - left.ts);
  const currentWork = activeWork.find((item) => item.role === view.active_role) ?? activeWork[0];
  const missionStatus = view.mission.status.toLowerCase();
  const missionRunning = ['working', 'grounding', 'framed'].includes(missionStatus);
  const missionDone = TERMINAL_MISSION_STATUSES.includes(missionStatus);
  const outcome = outcomeLabels(view.outcome, t)[0] ?? statusLabel(view.mission.status, t);
  const statusNarrative = needsAttention
    ? t(attentionKey)
    : missionDone
      ? t('mission.statusDone', {
          outcome,
          elapsed: formatMissionElapsed(view.mission.elapsed_seconds),
        })
      : missionRunning && currentWork
        ? t('mission.statusActive', {
            role: roleLabel(view.active_role || currentWork.role, t),
            work: currentWork.title,
          })
        : t('mission.statusWaiting');
  const statusTone = healthNeedsAttention || missionFailed || stepFailed
    ? 'error'
    : missionPaused
      ? 'waiting'
      : missionDone
        ? 'done'
        : missionRunning && currentWork
          ? 'active'
          : 'waiting';
  useEffect(() => setReplayIndex(Math.max(0, view.timeline.length - 1)), [view.timeline.length]);
  useEffect(() => {
    if (activeNode?.id) setSelectedTaskId(activeNode.id);
  }, [activeNode?.id]);
  const handleResume = async () => {
    if (resumeBusy) return;
    setResumeBusy(true);
    try {
      await api.setContinuous(sid, true, snapshot?.continuous?.objective ?? '');
    } catch {
      // ignore
    } finally {
      setResumeBusy(false);
    }
  };
  const replayRows = view.timeline.slice(0, replayIndex + 1).slice(-12).reverse();
  const selectedTask = view.dag.find((node) => node.id === selectedTaskId);
  const selectedRoleWork = view.role_work
    .filter((item) => item.role === selectedRole)
    .filter((item) => !selectedTaskId || !item.item_id || item.item_id === selectedTaskId)
    .slice(-40)
    .reverse();
  return (
    <section className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto bg-panel scroll-thin" aria-label={t('mission.control')}>
      <header className="border-b border-line/60 px-5 py-5">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">{t('mobile.mission')}</div>
        <div
          role="heading"
          aria-level={1}
          className="mt-1 line-clamp-4 max-w-4xl text-lg font-semibold leading-snug text-ink"
          title={objective}
        >
          <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{objective}</MarkdownContent>
        </div>
        {objective.length > 600 ? (
          <details className="mt-2 text-xs text-ink-faint">
            <summary className="cursor-pointer hover:text-ink">{t('mission.showObjective')}</summary>
            <div className="mt-2 text-ink-dim"><MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>{objective}</MarkdownContent></div>
          </details>
        ) : null}
        <div
          className="mission-status-line"
          data-tone={statusTone}
          role={needsAttention ? 'alert' : 'status'}
        >
          <div className="mission-status-line__signal">
            <span className="mission-status-line__marker" aria-hidden="true" />
            <span>{statusNarrative}</span>
          </div>
          {view.frontier.change ? (
            <div className="mission-status-line__subtitle">{view.frontier.change}</div>
          ) : null}
        </div>
        {view.mission.summary ? (
          <div className="mt-3 rounded border border-ok/25 bg-ok/5 px-3 py-2">
            <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ok">
              {t('mission.summary')}
            </div>
            <div className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-ink-dim">
              <MarkdownContent artifacts={artifacts} onOpenArtifact={onOpenArtifact}>
                {view.mission.summary}
              </MarkdownContent>
            </div>
          </div>
        ) : null}
        {delivery ? (
          <div className="mt-3 flex flex-wrap items-center gap-3 rounded border border-ok/30 bg-ok/5 px-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ok">
                {t(delivery.kind === 'submission_certified' ? 'mission.deliveryCertified' : 'mission.taskCompleted')}
              </div>
              <div className="mt-1 truncate text-xs text-ink-dim" title={delivery.summary || delivery.title}>
                {delivery.summary || delivery.title}
              </div>
            </div>
            {onOpenDelivery ? (
              <button
                type="button"
                onClick={() => onOpenDelivery(delivery)}
                title={delivery.primary_target
                  ? artifactByPath.get(delivery.primary_target.path)?.storage_path || delivery.primary_target.path
                  : delivery.title}
                className="shrink-0 rounded border border-ok/40 px-2 py-1 font-mono text-[10px] text-ok hover:border-ok"
              >
                {t(delivery.primary_target ? 'mission.openResult' : 'mission.viewTask')}
              </button>
            ) : null}
          </div>
        ) : null}
      </header>

      {snapshot?.continuous?.done_at && (
        <div className="mb-3 flex items-center gap-3 rounded-lg border-l-2 border-blue bg-blue/5 px-3 py-2">
          <span className="text-base">↩</span>
          <span className="min-w-0 flex-1 truncate text-sm text-ink-dim">
            {t('mission.continuousDone')}
            {snapshot.continuous.objective ? ` · ${snapshot.continuous.objective}` : ''}
          </span>
          <button
            type="button"
            disabled={resumeBusy}
            onClick={() => void handleResume()}
            className="compact-control shrink-0 px-3"
          >
            {resumeBusy ? '…' : t('mission.resumeContinuous')}
          </button>
        </div>
      )}

      <Achievement view={view} />

      <section className="border-b border-line/60 px-5 py-4">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">{t('mission.team')}</div>
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
                  <span className="text-xs font-semibold" style={{ color }}>{roleLabel(name, t)}</span>
                </div>
                <div className={`mt-1 truncate text-xs ${rejected ? 'text-err' : 'text-ink-dim'}`}>{role?.label || t('mission.waitingShort')}</div>
              </button>
            );
          })}
        </div>
      </section>

      <section className="border-b border-line/60 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
            {t('mission.roleWork')} · <span className="text-blue-sky">{roleLabel(selectedRole, t)}</span>
          </div>
          {selectedTask ? (
            <button type="button" onClick={() => setSelectedTaskId('')} className="text-[10px] text-ink-faint hover:text-ink">
              {t('mission.filteredBy', { task: selectedTask.title || selectedTask.objective || t('task.untitled') })}
            </button>
          ) : <span className="text-[10px] text-ink-faint">{t('mission.allVisible')}</span>}
        </div>
        <div className="mt-3 grid gap-2 lg:grid-cols-2">
          {selectedRoleWork.map((item) => {
            const status = item.status.toLowerCase();
            const active = status === 'active';
            const done = status === 'done';
            const failed = ['failed', 'error'].includes(status);
            const badgeLabel = done
              ? t('mission.done')
              : statusLabel(active ? 'active' : failed ? 'failed' : item.status, t);
            const isoTimestamp = new Date(item.ts * 1000).toISOString();
            return (
              <article key={item.id} className="min-w-0 rounded border border-line/60 bg-bg/35 px-3 py-2">
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate text-xs font-medium text-ink">{item.title}</span>
                  <time
                    dateTime={isoTimestamp}
                    title={isoTimestamp}
                    className="shrink-0 text-[11px] text-ink-dim"
                  >
                    {formatRelativeTime(item.ts, locale)}
                  </time>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-ink-faint">
                  <span className={`rounded-full border px-2 py-0.5 font-medium ${active ? 'border-blue/30 bg-blue/10 text-blue-sky' : done ? 'border-ok/30 bg-ok/10 text-ok' : failed ? 'border-err/30 bg-err/10 text-err' : 'border-line bg-white/[0.03] text-ink-dim'}`}>
                    {badgeLabel}
                  </span>
                  {item.round_index != null ? <span>{t('mission.roundNumber', { count: item.round_index })}</span> : null}
                </div>
                {item.detail ? <RoleWorkDetail detail={item.detail} /> : null}
              </article>
            );
          })}
          {!selectedRoleWork.length ? (
            <div className="col-span-full py-8 text-center text-xs text-ink-faint">
              {t('mission.noRoleWork', { role: roleLabel(selectedRole, t) })}
            </div>
          ) : null}
        </div>
      </section>

      <div className="grid min-h-[320px] border-b border-line/60 lg:grid-cols-[minmax(0,1.15fr)_minmax(260px,0.85fr)]">
        <section className="min-w-0 border-b border-line/60 px-5 py-4 lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between">
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">{t('mission.researchDag')}</div>
            {activeNode ? <span className="max-w-48 truncate text-[10px] text-blue-sky">{t('mission.active')} · {activeNode.title}</span> : null}
          </div>
          <div className="mt-3 space-y-0">
            {dagView.hidden.length ? (
              <div className="mb-3 rounded border border-line/60 bg-bg/50 px-3 py-2 text-[10px] text-ink-faint">
                {t('mission.hiddenTasks', {
                  count: dagView.hidden.length,
                  failed: dagView.hidden.filter((node) => ['failed', 'blocked'].includes(node.status)).length,
                  skipped: dagView.hidden.filter((node) => node.status === 'skipped').length,
                })}
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
                    <div className={`truncate text-xs font-medium ${active ? 'text-blue-sky' : 'text-ink'}`}>{node.title || node.objective || t('task.untitled')}</div>
                    <div className="mt-0.5 flex gap-2 text-[10px] text-ink-faint"><span>{statusLabel(node.status, t)}</span>{node.deps.length ? <span>{t('mission.startsAfter', { count: node.deps.length })}</span> : null}</div>
                  </div>
                </button>
              );
            }) : <div className="py-12 text-center text-xs text-ink-faint">{t('mission.noDag')}</div>}
          </div>
          {selectedTask ? (
            <div className="mt-3 rounded border border-blue/25 bg-blue/5 px-3 py-3">
              <div className="text-xs font-semibold text-blue-sky">{selectedTask.title || selectedTask.objective || t('task.untitled')}</div>
              {selectedTask.objective ? <p className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-ink-dim">{selectedTask.objective}</p> : null}
              {selectedTask.plan_hypothesis ? (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">{t('mission.workingHypothesis')}</div>
                  <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-ink-dim">{selectedTask.plan_hypothesis}</p>
                </div>
              ) : null}
              {selectedTask.goal_contribution ? (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">{t('mission.goalContribution')}</div>
                  <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-ink-dim">{selectedTask.goal_contribution}</p>
                </div>
              ) : null}
              {selectedTask.expected_regressions ? (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">{t('mission.temporaryRegressions')}</div>
                  <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-ink-dim">{selectedTask.expected_regressions}</p>
                </div>
              ) : null}
              {selectedTask.decision_rule ? (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">{t('mission.decisionRule')}</div>
                  <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-ink-dim">{selectedTask.decision_rule}</p>
                </div>
              ) : null}
              {selectedTask.acceptance_check ? (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">{t('mission.acceptance')}</div>
                  <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-ink-dim">{selectedTask.acceptance_check}</p>
                </div>
              ) : null}
              {selectedTask.non_goals?.length ? (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">{t('mission.nonGoals')}</div>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-ink-dim">
                    {selectedTask.non_goals.map((goal) => <li key={goal}>{goal}</li>)}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>

        <section className="min-w-0 px-5 py-4">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">{t('mission.capabilities')}</div>
          {view.learned_skills.length ? (
            <div className="mt-3">
              <div className="text-[10px] uppercase tracking-[0.12em] text-ok">{t('mission.capabilitiesUnlocked')}</div>
              <div className="mt-2 space-y-2">
                {view.learned_skills.filter((skill) => skill.status === 'active').slice(-8).map((skill) => (
                  <details key={String(skill.id)} className="rounded border border-ok/35 bg-ok/5 px-2 py-1.5">
                    <summary className="cursor-pointer text-[10px] text-ok">{String(skill.name || t('mission.learnedCapability'))}</summary>
                    {skill.mission_title ? <div className="mt-2 text-[9px] text-ink-faint">{t('mission.learnedDuring', { mission: skill.mission_title })}</div> : null}
                    {skill.content ? (
                      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap border-t border-ok/20 pt-2 font-mono text-[10px] leading-5 text-ink-dim scroll-thin">
                        {skill.content}{skill.content_truncated ? '\n… content truncated' : ''}
                      </pre>
                    ) : <div className="mt-2 text-[10px] text-ink-faint">{t('mission.skillUnavailable')}</div>}
                  </details>
                ))}
              </div>
            </div>
          ) : null}
          {view.learned_wiki_pages.some((page) => page.status !== 'retired') ? (
            <div className="mt-4 border-t border-line/50 pt-3">
              <div className="text-[10px] uppercase tracking-[0.12em] text-blue-sky">{t('mission.knowledgeRetained')}</div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {view.learned_wiki_pages.filter((page) => page.status !== 'retired').slice(-6).map((page) => <span key={String(page.id)} className="rounded border border-blue/35 bg-blue/5 px-2 py-1 text-[10px] text-blue-sky">{String(page.title || page.id)}</span>)}
              </div>
            </div>
          ) : null}
          {(view.storage.project_skill_dir || view.storage.global_skill_dir || view.storage.wiki_paths.length || view.storage.skill_history_compressed || view.storage.wiki_retired_compressed) ? (
            <div className="mt-4 border-t border-line/50 pt-3">
              <div className="text-[10px] uppercase tracking-[0.12em] text-ink-faint">{t('mission.selfEvolution')}</div>
              <div className="mt-2 text-[10px] text-ink-dim">{t('mission.knowledgeSaved')}</div>
            </div>
          ) : null}
        </section>
      </div>

      <section className="px-5 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">{t('mission.replay')}</div>
          {view.timeline.length > 1 ? (
            <input
              type="range"
              min={0}
              max={view.timeline.length - 1}
              value={replayIndex}
              onChange={(event) => setReplayIndex(Number(event.target.value))}
              aria-label={t('mission.replayTimeline')}
              className="h-1 min-w-32 flex-1 accent-blue"
            />
          ) : null}
          {replayRows.length ? (
            <span className="text-[10px] text-ink-faint">
              {t(replayRows.length === 1 ? 'mission.showingLatestEvent' : 'mission.showingLastEvents', { count: replayRows.length })}
            </span>
          ) : null}
        </div>
        <div className="mt-3 space-y-3">
          {replayRows.map((item) => {
            const date = new Date(item.ts * 1000);
            const color = theme.role[item.role] ?? theme.inkFaint;
            return (
              <article key={item.id} className="rounded border border-line/60 bg-bg/35 px-3 py-2.5 text-xs">
                <div className="flex items-start gap-2">
                  <span
                    aria-hidden="true"
                    className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${item.tone === 'error' ? 'bg-err' : item.tone === 'success' || item.tone === 'metric' || item.tone === 'skill' ? 'bg-ok' : 'bg-blue'}`}
                  />
                  <span
                    className="shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium"
                    style={{ borderColor: color, color }}
                  >
                    {missionRoleLabel(item.role, t)}
                  </span>
                  <span className="min-w-0 flex-1 break-words font-medium leading-5 text-ink">{item.title}</span>
                  <time
                    dateTime={date.toISOString()}
                    title={date.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' })}
                    className="shrink-0 font-mono text-[10px] text-ink-faint"
                  >
                    {formatMissionEventTime(item.ts, locale)}
                  </time>
                </div>
                {item.detail ? (
                  <p className="mt-2 whitespace-pre-wrap break-words leading-5 text-ink-dim">{item.detail}</p>
                ) : null}
              </article>
            );
          })}
          {!view.timeline.length ? <div className="py-10 text-center text-xs text-ink-faint">{t('mission.waitingEvents')}</div> : null}
        </div>
        {view.artifacts.length ? (
          <div className="mt-5 flex flex-wrap gap-2 border-t border-line/50 pt-4">
            {view.artifacts.slice(-8).map((artifact) => {
              const path = String(artifact.path || '');
              const info = artifactByPath.get(path);
              return (
                <button
                  key={String(artifact.id || path)}
                  type="button"
                  disabled={!path || !onOpenArtifact || info?.exists === false}
                  onClick={() => path && onOpenArtifact?.(path)}
                  title={info?.storage_path || path}
                  className="rounded border border-line px-2 py-1 font-mono text-[10px] text-blue-sky hover:border-blue-sky/50 disabled:text-ink-faint"
                >
                  {String(artifact.title || t('research.artifact'))}
                </button>
              );
            })}
          </div>
        ) : null}
        {gitDiff?.available && (gitDiff.status || gitDiff.diff) ? (
          <div className="mt-5 border-t border-line/50 pt-4 text-[10px] text-ink-faint">
            <span className="font-semibold uppercase tracking-[0.14em]">{t('mission.projectFilesChanged')}</span>
            <span> · {t('mission.reviewInIde')}</span>
          </div>
        ) : null}
      </section>
    </section>
  );
}
