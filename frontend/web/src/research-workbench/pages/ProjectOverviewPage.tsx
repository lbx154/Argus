import { ArrowRight, TimerReset } from 'lucide-react';
import { Badge, EventTimeline, Panel } from '../components/Common';
import { certificationLabel, roleLabel, stageLabel, statusLabel } from '../enumLabels';
import { formatDuration, statusTone } from '../utils';
import { useWorkbenchText } from '../useWorkbenchText';
import type { ActiveWorkbenchPageProps } from './pageTypes';
import { WORKSPACE_DESTINATIONS } from '../modules';

export function ProjectOverviewPage(props: ActiveWorkbenchPageProps) {
  const { text } = useWorkbenchText();
  const view = props.snapshot.mission_view;
  const research = view?.routing.vertical === 'research';
  const activeRole = view?.active_role || props.status?.active_role || 'idle';
  const stageStatus = [
    statusLabel(view?.mission.status || 'idle', text),
    view?.outcome.stage_certification ? certificationLabel(view.outcome.stage_certification, text) : '',
  ].filter(Boolean).join(' · ');
  return (
    <div className="overview-page">
      <section className="overview-hero">
        <div className="overview-hero__copy">
          <div className="overview-hero__badges">
            <Badge tone={props.snapshot.daemon.alive ? 'live' : 'neutral'} dot>{props.snapshot.daemon.alive ? text('Argus 正在运行', 'Argus running') : text('Argus 已停止', 'Argus stopped')}</Badge>
            <Badge tone={statusTone(view?.stage.id)}>{view?.stage.label || stageLabel(view?.stage.id, text)}</Badge>
          </div>
          <h1>{props.snapshot.session.display_name || props.project.label}</h1>
          <p>{view?.mission.objective || props.status?.continuous?.objective || props.project.objective || text('尚未设置目标。', 'No objective has been set.')}</p>
        </div>
        <div className="overview-hero__stats">
          <div><span>{text('当前角色', 'Active role')}</span><strong>{roleLabel(activeRole, text)}</strong><small>{props.snapshot.roles.find((role) => role.active)?.label || statusLabel('waiting', text)}</small></div>
          <div><span>{research ? text('研究阶段', 'Research stage') : text('工作流阶段', 'Workflow stage')}</span><strong>{view?.stage.label || stageLabel(view?.stage.id, text)}</strong><small>{stageStatus}</small></div>
          <div><span>{text('累计运行', 'Elapsed')}</span><strong>{formatDuration(view?.mission.campaign_elapsed_seconds || props.snapshot.daemon.uptime_seconds)}</strong><small>{view?.round.current ? text(`第 ${view.round.current}/${view.round.max || '—'} 轮`, `Round ${view.round.current}/${view.round.max || '—'}`) : text('暂无轮次', 'No round')}</small></div>
        </div>
      </section>

      <div className="overview-section-heading"><div><h2>{text('项目工作区', 'Project workspace')}</h2><p>{text('所有模块共享同一个 Argus 项目、项目文件和实时动态。', 'All modules share the same Argus project, project files, and live activity.')}</p></div></div>
      <section className="module-grid">
        {WORKSPACE_DESTINATIONS.map((module) => {
          const Icon = module.icon;
          return (
            <button className="module-card" type="button" key={module.id} onClick={() => props.navigate(module.id)}>
              <span className={`module-card__icon module-card__icon--${module.color}`}><Icon size={20} /></span>
              <div><h3>{text(module.zh, module.en)}</h3><p>{text(module.zhDesc, module.enDesc)}</p></div>
              <ArrowRight size={16} />
            </button>
          );
        })}
      </section>

      <section className="overview-lower">
        <Panel eyebrow="CURRENT MISSION" title={text('当前任务', 'Current mission')}>
          <div className="overview-mission">
            <div><TimerReset size={18} /><span>{statusLabel(view?.mission.status || 'idle', text)}</span></div>
            <h3>{view?.mission.title || props.project.current_task || text('等待新任务', 'Waiting for a new task')}</h3>
            <p>{view?.mission.summary || view?.frontier.summary || view?.review.reason || text('Argus 的下一步和 Reviewer 边界会在这里同步。', 'Argus next steps and reviewer boundaries appear here.')}</p>
            <button className="button button--secondary" type="button" onClick={() => props.navigate('experiments')}>{text('查看完整实验进程', 'View experiment progress')} <ArrowRight size={14} /></button>
          </div>
        </Panel>
        <Panel eyebrow="RECENT ACTIVITY" title={text('最近活动', 'Recent activity')} bodyClassName="panel__body--flush">
          <EventTimeline events={props.events} limit={7} dense />
        </Panel>
      </section>
    </div>
  );
}
