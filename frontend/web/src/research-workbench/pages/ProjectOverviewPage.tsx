import { ArrowRight, BookOpen, Code2, FileText, FlaskConical, Inbox, Megaphone, MessagesSquare, ShieldCheck, TimerReset } from 'lucide-react';
import { Badge, EventTimeline, Panel } from '../components/Common';
import { formatDuration, statusTone } from '../utils';
import { useWorkbenchText } from '../useWorkbenchText';
import type { WorkspacePageProps } from './pageTypes';

const MODULES = [
  { id: 'experiments', zh: '运行进程', en: 'Execution', zhDesc: '实时查看 Argus 运行位置、DAG、角色交接和停止原因。', enDesc: 'Track Argus execution, DAG progress, role handoffs, and stop reasons.', icon: FlaskConical, color: 'blue', researchOnly: false },
  { id: 'copilot', zh: 'Argus Copilot', en: 'Argus Copilot', zhDesc: '查看 Argus 对话、Prompt 优化和工具轨迹。', enDesc: 'Chat with Argus, refine prompts, and inspect tool activity.', icon: MessagesSquare, color: 'violet', researchOnly: false },
  { id: 'literature', zh: '文献中心', en: 'Literature', zhDesc: '汇总已读论文、最近工作、检索记录和文献证据。', enDesc: 'Review papers, related work, retrieval history, and evidence.', icon: BookOpen, color: 'indigo', researchOnly: true },
  { id: 'inbox', zh: '科研收信箱', en: 'Research Inbox', zhDesc: '从零散输入抽取知识点并形成第一版 Argus Prompt。', enDesc: 'Turn rough notes into structured knowledge and an Argus prompt.', icon: Inbox, color: 'rose', researchOnly: true },
  { id: 'ide', zh: 'AI IDE', en: 'AI IDE', zhDesc: '连接真实服务器目录，查看代码、Git 和 Argus 活动。', enDesc: 'Browse server files, Git state, and Argus activity.', icon: Code2, color: 'emerald', researchOnly: false },
  { id: 'paper', zh: '论文工作区', en: 'Paper Workspace', zhDesc: '自动发现 Argus 新写入的文稿、BibTeX、图表和 PDF。', enDesc: 'Discover manuscripts, BibTeX, figures, and PDFs from the workspace.', icon: FileText, color: 'amber', researchOnly: true },
  { id: 'reviewer', zh: '模拟审稿', en: 'Reviewer', zhDesc: '区分每轮过程审稿与项目完成后的最终投稿前审稿。', enDesc: 'Separate round-level review from final pre-submission review.', icon: ShieldCheck, color: 'slate', researchOnly: true },
  { id: 'release', zh: '成果发布', en: 'Release', zhDesc: '规划 GitHub 仓库、学术海报和项目宣传页。', enDesc: 'Plan a GitHub repository, academic poster, and project page.', icon: Megaphone, color: 'rose', researchOnly: true },
] as const;

export function ProjectOverviewPage(props: WorkspacePageProps) {
  const { text } = useWorkbenchText();
  const view = props.snapshot.mission_view;
  const research = view?.routing.vertical === 'research';
  const modules = research ? MODULES : MODULES.filter((module) => !module.researchOnly);
  const activeRole = view?.active_role || props.status?.active_role || 'idle';
  const stageStatus = view?.outcome.stage_certification
    ? `${view.mission.status || 'idle'} · ${text('阶段', 'stage')} ${view.outcome.stage_certification}`
    : view?.mission.status || 'idle';
  return (
    <div className="overview-page">
      <section className="overview-hero">
        <div className="overview-hero__copy">
          <div className="overview-hero__badges">
            <Badge tone={props.snapshot.daemon.alive ? 'live' : 'neutral'} dot>{props.snapshot.daemon.alive ? text('Argus 正在运行', 'Argus running') : text('Argus 已停止', 'Argus stopped')}</Badge>
            <Badge tone={statusTone(view?.stage.id)}>{view?.stage.label || text('未分阶段', 'Unstaged')}</Badge>
          </div>
          <h1>{props.snapshot.session.display_name || props.project.label}</h1>
          <p>{view?.mission.objective || props.status?.continuous?.objective || props.project.objective || text('尚未设置目标。', 'No objective has been set.')}</p>
          <code>{props.snapshot.session.workdir || props.snapshot.session.launch_cwd}</code>
        </div>
        <div className="overview-hero__stats">
          <div><span>{text('当前角色', 'Active role')}</span><strong>{activeRole}</strong><small>{props.snapshot.roles.find((role) => role.active)?.label || 'waiting'}</small></div>
          <div><span>{research ? text('研究阶段', 'Research stage') : text('工作流阶段', 'Workflow stage')}</span><strong>{view?.stage.label || '—'}</strong><small>{stageStatus}</small></div>
          <div><span>{text('累计运行', 'Elapsed')}</span><strong>{formatDuration(view?.mission.campaign_elapsed_seconds || props.snapshot.daemon.uptime_seconds)}</strong><small>{view?.round.current ? `Round ${view.round.current}/${view.round.max || '—'}` : text('暂无轮次', 'No round')}</small></div>
        </div>
      </section>

      <div className="overview-section-heading"><div><h2>{text('项目工作区', 'Project workspace')}</h2><p>{text('所有模块共享同一个 Argus 项目、工作目录和实时事件流。', 'All modules share the same Argus project, workdir, and live event stream.')}</p></div></div>
      <section className="module-grid">
        {modules.map((module) => {
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
            <div><TimerReset size={18} /><span>{view?.mission.status || 'idle'}</span></div>
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
