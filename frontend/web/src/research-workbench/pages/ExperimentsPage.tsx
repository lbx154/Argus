import { Activity, AlertTriangle, Check, Circle, Clock3, Pause, Play, RefreshCw, ShieldCheck, Square, Workflow } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Badge, EmptyState, EventTimeline } from '../components/Common';
import { roleLabel, statusLabel } from '../enumLabels';
import { AGENT_ROLES, agentRoleColor, agentRoleDescription } from '../../lib/agentRoles';
import { isBookkeepingEvent, plainDetail, plainStage } from '../../lib/plainStatus';
import { workStatusLabel } from '../../lib/workStatus';
import { readableToolProgress } from '../../lib/feedSteps';
import { RawDisclosure } from '../../components/primitives';
import { deriveProgressEstimate } from '../progressEstimate';
import type { MissionDagNode } from '../types';
import { formatDuration, statusTone } from '../utils';
import { useWorkbenchText } from '../useWorkbenchText';
import type { ActiveWorkbenchPageProps } from './pageTypes';

const DONE = new Set(['done', 'completed', 'accepted', 'success']);
const ACTIVE = new Set(['running', 'in_progress', 'claimed', 'active', 'working']);

export function ExperimentsPage(props: ActiveWorkbenchPageProps) {
  const { locale, text, t } = useWorkbenchText();
  const [now, setNow] = useState(() => Date.now() / 1_000);
  const [selectedTask, setSelectedTask] = useState('');
  useEffect(() => {
    if (!props.active || !props.snapshot.daemon.alive) return;
    setNow(Date.now() / 1_000);
    const timer = window.setInterval(() => setNow(Date.now() / 1_000), 1_000);
    return () => clearInterval(timer);
  }, [props.active, props.snapshot.daemon.alive]);
  const progress = useMemo(() => deriveProgressEstimate(props.snapshot, props.events, now, locale), [locale, now, props.events, props.snapshot]);
  const view = props.snapshot.mission_view;
  const dag: MissionDagNode[] = view?.dag?.length ? view.dag : props.snapshot.backlog.map((item) => ({ id: item.id, title: item.title, objective: item.objective, status: item.status, deps: item.deps ?? [], branch_id: item.id, parent_branch_id: '' }));
  const selected = dag.find((task) => task.id === selectedTask) ?? dag.find((task) => task.id === progress.currentTaskId);
  const currentStage = plainStage(view?.stage.id, locale);
  const stageName = view?.stage.label && view.stage.label !== view.stage.id ? view.stage.label
    : currentStage || view?.stage.id || text('尚未记录', 'Not yet recorded');
  const running = progress.runtime.state === 'running';
  const runtimeLabel = workStatusLabel(progress.runtime, locale);
  const recentEvents = progress.taskEvents.filter((event) => String(event.kind ?? '') !== 'reasoning' && !isBookkeepingEvent(String(event.type ?? '')));
  const currentDetail = plainDetail(progress.currentDetail, locale);
  const toolProgress = progress.currentEvent ? readableToolProgress(progress.currentEvent, locale) : null;
  const reviewDetail = plainDetail(progress.review.detail, locale);
  const taskGroups = [
    { label: text('已完成的工作', 'Completed work'), items: dag.filter((task) => DONE.has(task.status)) },
    { label: text('进行中与待处理', 'Active and remaining work'), items: dag.filter((task) => !DONE.has(task.status)) },
  ];

  const stop = async (drain: boolean) => {
    const message = drain
      ? text('确认完成当前步骤后停止 Argus？', 'Stop Argus after the current step finishes?')
      : text('确认立即停止 Argus？当前步骤可能被中断。', 'Stop Argus now? The current step may be interrupted.');
    if (confirm(message)) await props.controls.stop(drain);
  };

  return (
    <div className="ros-page experiment-v3">
      <header className="ros-page-header">
        <div><h1>{text('任务进展', 'Work progress')}</h1><p>{text('看清做了哪些工作、结论核对到了哪里，以及团队接下来要做什么。', 'See what work was done, which conclusions were checked, and what the team needs to do next.')}</p></div>
        <div className="experiment-header-actions"><button className="button button--secondary" type="button" onClick={() => void props.refresh()}><RefreshCw size={14} />{text('刷新', 'Refresh')}</button>{!(props.snapshot.daemon.alive && props.snapshot.daemon.control_available === false) ? props.snapshot.daemon.alive ? <><button className="button button--secondary" type="button" disabled={props.controls.busy} onClick={() => void stop(true)}><Pause size={14} />{text('当前步后停止', 'Stop after step')}</button><button className="button button--danger" type="button" disabled={props.controls.busy} onClick={() => void stop(false)}><Square size={13} />{text('立即停止', 'Stop now')}</button></> : <button className="button button--primary" type="button" disabled={props.controls.busy} onClick={() => void props.controls.start()}><Play size={14} />{text('继续运行', 'Resume')}</button> : null}</div>
      </header>

      <section className="experiment-progress-hero">
        <div className="progress-hero-main">
          <div className="progress-live-line"><Badge tone={running ? 'live' : 'neutral'} dot={running}>{runtimeLabel}</Badge>{progress.runtime.role ? <span>{roleLabel(progress.runtime.role, text)}</span> : null}</div>
          <h2>{progress.currentTask}</h2>
          {progress.currentObjective ? <p className="work-objective">{progress.currentObjective}</p> : null}
          <div className="current-step-callout"><span><Activity size={17} /></span><div><small>{running ? text('当前动作', 'Current action') : text('当前状态与最近记录', 'Current state and latest record')}</small><strong>{toolProgress?.title || progress.currentStep}</strong>{toolProgress ? <><p>{toolProgress.detail}</p><RawDisclosure label={text('原始调用记录', 'Original call record')}><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs">{progress.currentDetail}</pre></RawDisclosure></> : progress.currentDetail ? <p title={currentDetail.technical || undefined}>{currentDetail.text}</p> : null}</div></div>
        </div>
        <div className="progress-number"><span>{text('已完成的工作项', 'Completed work items')}</span><strong>{progress.completedTasks}<em> / {progress.totalTasks || '—'}</em></strong><small>{text('按当前记录计数', 'Counted from current records')}</small></div>
        <div className="truthful-progress">
          {progress.workCompletion !== null ? <div className="truthful-progress__track" role="progressbar" aria-label={text('已记录工作项的完成情况', 'Completion of recorded work items')} aria-valuemin={0} aria-valuemax={progress.totalTasks} aria-valuenow={progress.completedTasks} aria-valuetext={text(`${progress.totalTasks} 项已记录工作中完成 ${progress.completedTasks} 项`, `${progress.completedTasks} of ${progress.totalTasks} recorded work items completed`)}><span className="confirmed" style={{ width: `${progress.workCompletion * 100}%` }} /></div> : null}
          <p className="work-scope">{progress.workScope}</p>
        </div>
        <div className="progress-metrics">
          <div><span><Clock3 size={15} />{text('当前工作项已用时间', 'Current work item elapsed')}</span><strong>{progress.elapsedSeconds === null ? '—' : formatDuration(progress.elapsedSeconds)}</strong><small>{text('按任务启动与结束记录计算', 'Based on recorded task start and end times')}</small></div>
          <div><span>{text('实际记录的阶段', 'Recorded stage')}</span><strong>{stageName}</strong><small>{text('阶段名称不表示前面的工作已通过核验', 'The stage name does not certify earlier work')}</small></div>
          <div><span>{text('工作范围', 'Work scope')}</span><strong>{progress.openEnded ? text('开放研究', 'Open research') : text('当前工作项', 'Current work item')}</strong><small>{progress.openEnded ? text('可能产生新的问题和任务', 'New questions and tasks may emerge') : text('以该工作项的验收条件为准', 'Defined by this item’s acceptance criteria')}</small></div>
          <div><span><Workflow size={15} />{text('等待执行的工作', 'Work waiting to start')}</span><strong>{progress.pendingTasks}</strong><small>{text('已记录清单中的等待项', 'Waiting items in the recorded list')}</small></div>
        </div>
      </section>

      <div className="experiment-v3-grid">
        <aside className="ros-card experiment-task-route"><header><div><h2>{text('工作项', 'Work items')}</h2></div><Badge tone="neutral">{dag.length}</Badge></header><div>{dag.length ? taskGroups.map((group) => <section className="work-item-group" key={group.label}><h3>{group.label}<span>{group.items.length}</span></h3>{group.items.length ? group.items.map((task) => {
          const active = running && task.id === progress.currentTaskId && ACTIVE.has(task.status);
          const displayedStatus = ACTIVE.has(task.status) && !active ? task.id === progress.currentTaskId ? runtimeLabel : text('等待状态确认', 'Awaiting status confirmation') : statusLabel(task.status, text);
          return <button type="button" key={task.id} className={selected?.id === task.id ? 'is-active' : ''} onClick={() => setSelectedTask(task.id)}><span className={`task-state task-state--${DONE.has(task.status) ? 'success' : active ? 'live' : 'neutral'}`}>{DONE.has(task.status) ? <Check size={12} /> : active ? <Activity size={12} /> : <Circle size={9} />}</span><div><strong>{task.title || task.objective || text('未命名任务', 'Untitled task')}</strong><small>{displayedStatus}{task.deps.length ? text(` · 前置工作 ${task.deps.length} 项`, ` · ${task.deps.length} dependencies`) : ''}</small></div></button>;
        }) : <p className="work-group-empty">{text('暂无记录', 'No records yet')}</p>}</section>) : <EmptyState icon={Workflow} title={text('尚无工作项', 'No work items yet')} />}</div></aside>

        <main className="experiment-v3-center">
          <section className="ros-card work-conclusion"><header><div><h2>{text('本轮结论与待核对问题', 'This round’s conclusions and open checks')}</h2></div><Badge tone={progress.review.state === 'passed' ? 'success' : progress.review.state === 'running' ? 'live' : 'warn'}>{progress.review.label}</Badge></header><div className="work-conclusion__body"><p title={reviewDetail.technical || undefined}>{reviewDetail.text}</p>{progress.acceptanceCriteria ? <div><h3>{text('判断这项工作是否完成的标准', 'How this work item is accepted')}</h3><p>{progress.acceptanceCriteria}</p></div> : null}{progress.nextAction ? <div><h3>{text('下一步要处理', 'What needs to happen next')}</h3><p>{progress.nextAction}</p></div> : null}<p className="work-scope">{progress.review.scope}</p></div></section>
          <section className="ros-card checkpoint-card"><header><div><h2>{text('当前工作项的过程记录', 'Current work item’s recorded steps')}</h2></div><Badge tone="neutral">{text('以记录为准', 'Based on records')}</Badge></header><div className="checkpoint-list">{progress.checkpoints.map((checkpoint, index) => <div className={`checkpoint checkpoint--${checkpoint.status}`} key={checkpoint.id}><span>{checkpoint.status === 'done' ? <Check size={13} /> : checkpoint.status === 'active' ? <Activity size={13} /> : checkpoint.status === 'blocked' ? <AlertTriangle size={13} /> : index + 1}</span><div><strong>{checkpoint.label}</strong><p>{checkpoint.detail}</p></div>{index < progress.checkpoints.length - 1 ? <i /> : null}</div>)}</div>{selected ? <div className="selected-task-detail"><span>{text('选中工作项', 'Selected work item')}</span><strong>{selected.title || selected.objective || text('未命名任务', 'Untitled task')}</strong><p>{selected.objective}</p></div> : null}</section>
          <section className="ros-card experiment-live-events"><header><div><h2>{text('当前工作项的最近动作', 'Recent actions for the current work item')}</h2></div><Badge tone={props.connected ? 'live' : 'warn'} dot>{props.connected ? text('实时', 'Live') : text('轮询中', 'Polling')}</Badge></header><EventTimeline events={recentEvents} limit={16} /></section>
        </main>

        <aside className="experiment-v3-side">
          <section className="ros-card experiment-team"><header><div><h2>{text('团队如何协作', 'How the team works together')}</h2></div></header><p className="work-team-note">{text('不同角色分工接力；是否正在执行，以当前状态记录为准。', 'Roles share the work and hand results to each other. Current status shows who is actually working.')}</p><div>{AGENT_ROLES.map((name) => {
            const role = props.snapshot.roles.find((item) => item.role === name);
            const active = running && progress.runtime.role === name;
            const idleStatus = role?.status && !ACTIVE.has(role.status) ? role.status : 'idle';
            return <article className={active ? 'is-active' : ''} key={name}><span data-role-dot={name} className="role-dot" style={{ backgroundColor: agentRoleColor(name) }} aria-hidden="true" /><div><strong>{roleLabel(name, text)}</strong><p>{agentRoleDescription(name, t)}</p></div>{active ? <Badge tone="live" dot>{text('执行中', 'Working')}</Badge> : <Badge tone={statusTone(idleStatus)}>{statusLabel(idleStatus, text)}</Badge>}</article>;
          })}</div></section>
          <section className="ros-card estimate-note"><header><div><h2>{text('还需要多久', 'How much longer?')}</h2></div></header><div><p><strong>{text('暂无法可靠预计', 'No reliable estimate yet')}</strong>{progress.etaUnavailableReason}</p><div className="work-evidence-note"><ShieldCheck size={15} /><span>{text('真实已用时间和已完成工作可以统计。读文件、运行命令或审查结束，都不能换算成整体目标的完成百分比。', 'Elapsed time and completed work can be counted. File reads, commands, and the end of a review do not establish a completion percentage for the overall goal.')}</span></div>{props.controls.error ? <div className="inline-error">{props.controls.error}</div> : null}</div></section>
        </aside>
      </div>
    </div>
  );
}
