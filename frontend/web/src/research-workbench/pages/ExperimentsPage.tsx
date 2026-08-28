import { Activity, AlertTriangle, Check, ChevronRight, Circle, Clock3, Gauge, Pause, Play, RefreshCw, ShieldCheck, Square, TimerReset, Workflow } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Badge, EmptyState, EventTimeline } from '../components/Common';
import { roleLabel, stageLabel, statusLabel } from '../enumLabels';
import { deriveProgressEstimate } from '../progressEstimate';
import type { MissionDagNode } from '../types';
import { formatDuration, statusTone } from '../utils';
import { useWorkbenchText } from '../useWorkbenchText';
import type { WorkspacePageProps } from './pageTypes';

const DONE = new Set(['done', 'completed', 'accepted', 'success']);
const ACTIVE = new Set(['running', 'in_progress', 'claimed', 'active', 'working']);
const ROLE_ORDER = ['manager', 'planner', 'engineer', 'reviewer'];
const STAGES = ['scope', 'research', 'implementation', 'experiment', 'analysis', 'writing', 'review'] as const;

function stageIndex(value: string): number {
  const stage = value.toLowerCase();
  if (/review|delivery/.test(stage)) return 6;
  if (/writ|draft|paper/.test(stage)) return 5;
  if (/analy|select/.test(stage)) return 4;
  if (/experiment|pilot|run|eval/.test(stage)) return 3;
  if (/implement|build|engineer/.test(stage)) return 2;
  if (/research|literature|idea/.test(stage)) return 1;
  return 0;
}

function percent(value: number | null) { return value == null ? '—' : `${Math.round(value * 100)}%`; }
function clockRange(now: number, min: number, max: number, locale: string) {
  const format = (seconds: number) => new Date((now + seconds) * 1_000).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: false });
  return `${format(min)}–${format(max)}`;
}

export function ExperimentsPage(props: WorkspacePageProps) {
  const { locale, text } = useWorkbenchText();
  const [now, setNow] = useState(() => Date.now() / 1_000);
  const [selectedTask, setSelectedTask] = useState('');
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now() / 1_000), 1_000); return () => clearInterval(timer); }, []);
  const estimate = useMemo(() => deriveProgressEstimate(props.snapshot, props.events, now, locale), [locale, now, props.events, props.snapshot]);
  const view = props.snapshot.mission_view;
  const dag: MissionDagNode[] = view?.dag?.length ? view.dag : props.snapshot.backlog.map((item) => ({ id: item.id, title: item.title, objective: item.objective, status: item.status, deps: item.deps ?? [], branch_id: item.id, parent_branch_id: '' }));
  const activeTask = dag.find((task) => ACTIVE.has(task.status)) ?? dag.find((task) => /pending|queued/.test(task.status)) ?? dag.at(-1);
  const selected = dag.find((task) => task.id === selectedTask) ?? activeTask;
  const currentStage = stageIndex(view?.stage.id || view?.stage.label || 'scope');
  const confirmedPct = Math.round(estimate.confirmed * 100);
  const minPct = Math.round((estimate.range?.[0] ?? estimate.confirmed) * 100);
  const maxPct = Math.round((estimate.range?.[1] ?? estimate.confirmed) * 100);
  const pointPct = Math.round((estimate.estimate ?? estimate.confirmed) * 100);
  const health = props.snapshot.daemon.health?.state || (props.snapshot.daemon.alive ? 'active' : 'stopped');
  const reviewRisk = view?.review?.status && !DONE.has(view.review.status) ? view.review : null;
  const recentEvents = props.events.filter((event) => String(event.kind ?? '') !== 'reasoning' && !String(event.type ?? '').startsWith('provider.'));

  const stop = async (drain: boolean) => {
    const message = drain
      ? text('确认完成当前步骤后停止 Argus？', 'Stop Argus after the current step finishes?')
      : text('确认立即停止 Argus？当前步骤可能被中断。', 'Stop Argus now? The current step may be interrupted.');
    if (confirm(message)) await props.controls.stop(drain);
  };

  return (
    <div className="ros-page experiment-v3">
      <header className="ros-page-header">
        <div><div className="eyebrow">EXPERIMENT PROGRESS</div><h1>{text('实验进程', 'Experiment progress')}</h1><p>{text('查看当前步骤、预计进度范围、预计完成时间，以及估算的可信程度。', 'See the current step, estimated progress range, expected finish time, and how confident Argus is in the estimate.')}</p></div>
        <div className="experiment-header-actions"><button className="button button--secondary" type="button" onClick={() => void props.refresh()}><RefreshCw size={14} />{text('刷新', 'Refresh')}</button>{props.snapshot.daemon.control_available !== false ? props.snapshot.daemon.alive ? <><button className="button button--secondary" type="button" disabled={props.controls.busy} onClick={() => void stop(true)}><Pause size={14} />{text('当前步后停止', 'Stop after step')}</button><button className="button button--danger" type="button" disabled={props.controls.busy} onClick={() => void stop(false)}><Square size={13} />{text('立即停止', 'Stop now')}</button></> : <button className="button button--primary" type="button" disabled={props.controls.busy} onClick={() => void props.controls.start()}><Play size={14} />{text('继续运行', 'Resume')}</button> : null}</div>
      </header>

      <section className="experiment-progress-hero">
        <div className="progress-hero-main">
          <div className="progress-live-line"><Badge tone={props.snapshot.daemon.alive ? 'live' : 'neutral'} dot>{props.snapshot.daemon.alive ? text('ARGUS 运行中', 'ARGUS RUNNING') : text('ARGUS 已停止', 'ARGUS STOPPED')}</Badge><span>{view?.stage.label || stageLabel(view?.stage.id, text)}</span><span>{roleLabel(estimate.currentRole, text)}</span></div>
          <h2>{estimate.currentTask}</h2>
          <div className="current-step-callout"><span><Activity size={17} /></span><div><small>{props.snapshot.daemon.alive ? text('当前正在进行', 'In progress') : text('最后执行位置', 'Last execution point')}</small><strong>{estimate.currentStep}</strong>{estimate.currentDetail ? <code>{estimate.currentDetail}</code> : null}</div></div>
        </div>
        <div className="progress-number"><span>{text('预计进度', 'Estimated progress')}</span><strong>{percent(estimate.estimate)}</strong><small>{text('预计范围', 'Likely range')} {minPct}–{maxPct}%</small></div>
        <div className="truthful-progress" aria-label={text(`预计完成 ${pointPct}%`, `Estimated completion ${pointPct}%`)}>
          <div className="truthful-progress__track"><span className="confirmed" style={{ width: `${confirmedPct}%` }} /><span className="estimated-range" style={{ left: `${minPct}%`, width: `${Math.max(1, maxPct - minPct)}%` }} /><i style={{ left: `${pointPct}%` }} /></div>
          <div className="truthful-progress__legend"><span><b className="confirmed-dot" />{text('确定完成', 'Confirmed')} {confirmedPct}%</span><span><b className="range-dot" />{text('估计范围', 'Estimated range')} {minPct}–{maxPct}%</span><span>{estimate.basis}</span></div>
        </div>
        <div className="progress-metrics">
          <div><span><Clock3 size={15} />{text('当前任务已运行', 'Current task elapsed')}</span><strong>{formatDuration(estimate.elapsedSeconds)}</strong><small>{text('从任务领取开始', 'Since task claim')}</small></div>
          <div><span><TimerReset size={15} />{text('预计完成时间', 'Expected finish time')}</span><strong>{estimate.eta ? `${formatDuration(estimate.eta.minSeconds)}–${formatDuration(estimate.eta.maxSeconds)}` : text('暂不可用', 'Unavailable')}</strong><small>{estimate.eta ? clockRange(now, estimate.eta.minSeconds, estimate.eta.maxSeconds, locale) : estimate.etaUnavailableReason}</small></div>
          <div><span><Gauge size={15} />{text('估算置信度', 'Estimate confidence')}</span><strong className={`confidence-${estimate.confidence}`}>{estimate.confidence === 'high' ? text('高', 'High') : estimate.confidence === 'medium' ? text('中', 'Medium') : text('低', 'Low')}</strong><small>{estimate.eta?.basis || text('需要更多历史任务', 'More task history is needed')}</small></div>
          <div><span><Workflow size={15} />{text('任务路线', 'Task route')}</span><strong>{estimate.completedTasks} / {estimate.totalTasks || '—'}</strong><small>{text(`${estimate.pendingTasks} 项等待中`, `${estimate.pendingTasks} waiting`)} · {text('当前步骤', 'current step')} {Math.round(estimate.currentFraction * 100)}%</small></div>
        </div>
      </section>

      <section className="research-stage-rail">
        {STAGES.map((id, index) => <div className={index < currentStage ? 'is-done' : index === currentStage ? 'is-active' : ''} key={id}><span>{index < currentStage ? <Check size={13} /> : index + 1}</span><strong>{stageLabel(id, text)}</strong>{index < STAGES.length - 1 ? <ChevronRight size={14} /> : null}</div>)}
      </section>

      <div className="experiment-v3-grid">
        <aside className="ros-card experiment-task-route"><header><div><span>MISSION ROUTE</span><h2>{text('任务路线', 'Task route')}</h2></div><Badge tone="neutral">{dag.length}</Badge></header><div>{dag.length ? dag.map((task) => <button type="button" key={task.id} className={selected?.id === task.id ? 'is-active' : ''} onClick={() => setSelectedTask(task.id)}><span className={`task-state task-state--${statusTone(task.status)}`}>{DONE.has(task.status) ? <Check size={12} /> : ACTIVE.has(task.status) ? <Activity size={12} /> : <Circle size={9} />}</span><div><strong>{task.title || task.objective || text('未命名任务', 'Untitled task')}</strong><small>{statusLabel(task.status, text)}{task.deps.length ? text(` · 需等待前置任务 ${task.deps.length} 项`, ` · Starts after ${task.deps.length} earlier tasks`) : ''}</small></div></button>) : <EmptyState icon={Workflow} title={text('尚无任务路线', 'No task route yet')} />}</div></aside>

        <main className="experiment-v3-center">
          <section className="ros-card checkpoint-card"><header><div><span>CURRENT CHECKPOINTS</span><h2>{text('当前任务走到哪一步', 'Current task checkpoints')}</h2></div><Badge tone="info">{Math.round(estimate.currentFraction * 100)}%</Badge></header><div className="checkpoint-list">{estimate.checkpoints.map((checkpoint, index) => <div className={`checkpoint checkpoint--${checkpoint.status}`} key={checkpoint.id}><span>{checkpoint.status === 'done' ? <Check size={13} /> : checkpoint.status === 'active' ? <Activity size={13} /> : checkpoint.status === 'blocked' ? <AlertTriangle size={13} /> : index + 1}</span><div><strong>{checkpoint.label}</strong><p>{checkpoint.detail}</p></div>{index < estimate.checkpoints.length - 1 ? <i /> : null}</div>)}</div>{selected ? <div className="selected-task-detail"><span>{text('当前选择任务', 'Selected task')}</span><strong>{selected.title || selected.objective || text('未命名任务', 'Untitled task')}</strong><p>{selected.objective}</p></div> : null}</section>
          <section className="ros-card experiment-live-events"><header><div><span>LIVE EXECUTION</span><h2>{text('最近关键动作', 'Recent key actions')}</h2></div><Badge tone={props.connected ? 'live' : 'warn'} dot>{props.connected ? text('实时', 'Live') : text('轮询中', 'Polling')}</Badge></header><EventTimeline events={recentEvents} limit={16} /></section>
        </main>

        <aside className="experiment-v3-side">
          <section className="ros-card experiment-team"><header><div><span>ARGUS TEAM</span><h2>{text('角色交接', 'Role handoffs')}</h2></div></header><div>{ROLE_ORDER.map((name) => { const role = props.snapshot.roles.find((item) => item.role === name); return <article className={role?.active ? 'is-active' : ''} key={name}><span className={`role-dot role-dot--${name}`} /><div><strong>{roleLabel(name, text)}</strong><p>{role?.label || statusLabel('waiting', text)}</p><small>{statusLabel(role?.status || 'idle', text)}</small></div>{role?.active ? <Badge tone="live" dot>{statusLabel('active', text)}</Badge> : <Badge tone={statusTone(role?.status)}>{statusLabel(role?.status || 'idle', text)}</Badge>}</article>; })}</div></section>
          <section className="ros-card estimate-note"><header><div><span>ESTIMATE HEALTH</span><h2>{text('估算与风险', 'Estimate and risk')}</h2></div></header><div><p><strong>{text('估算说明', 'Estimate note')}</strong>{text('当前百分比根据任务状态和事件里程碑估算，可能随新进展调整。', 'The percentage is estimated from task state and event milestones and may change as work progresses.')}</p>{reviewRisk ? <div className="estimate-risk"><AlertTriangle size={15} /><span><strong>{roleLabel('reviewer', text)} · {statusLabel(reviewRisk.status, text)}</strong>{reviewRisk.reason || text('任务范围可能变化，预计完成时间已暂停更新。', 'Scope may change, so the expected finish time is paused.')}</span></div> : <div className="estimate-ok"><ShieldCheck size={15} /><span><strong>{text('当前估算可用', 'Estimate available')}</strong>{statusLabel(health, text)} · {text('最近进度', 'last progress')} {formatDuration(props.snapshot.daemon.health?.seconds_since_progress)}</span></div>}{props.controls.error ? <div className="inline-error">{props.controls.error}</div> : null}</div></section>
        </aside>
      </div>
    </div>
  );
}
