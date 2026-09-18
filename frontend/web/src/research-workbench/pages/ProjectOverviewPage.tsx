import { ArrowRight } from 'lucide-react';
import { SessionWorkdir } from '../../components/SessionWorkdir';
import { useWorkbenchText } from '../useWorkbenchText';
import type { ActiveWorkbenchPageProps } from './pageTypes';
import { WORKSPACE_DESTINATIONS } from '../modules';

// The overview says only what no other page says: the project's name, its
// full objective, the session directory, and what the workspace modules are for.
// Status, the current task and recent activity already have their own places.
export function ProjectOverviewPage(props: ActiveWorkbenchPageProps) {
  const { text } = useWorkbenchText();
  const view = props.snapshot.mission_view;
  const objective = view?.mission.objective || props.status?.continuous?.objective || props.project.objective || '';
  return (
    <div className="overview-page">
      <section className="overview-hero overview-hero--plain">
        <div className="overview-hero__copy">
          <h1>{props.snapshot.session.display_name || props.project.label}</h1>
          <p>{objective || text('尚未设置目标。', 'No objective has been set.')}</p>
          <SessionWorkdir session={props.snapshot.session} />
        </div>
      </section>

      <div className="overview-section-heading"><div><h2>{text('项目工作区', 'Project workspace')}</h2><p>{text('这些入口展示同一项目的排期、运行进程与代码工作区。', 'These views show the same project’s timeline, execution and code workspace.')}</p></div></div>
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
    </div>
  );
}
