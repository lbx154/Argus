import { useEffect, useState } from 'react';
import { Activity, Check, ChevronDown, Clock3, FileText, Pause, Terminal, X } from 'lucide-react';
import type { EventMsg, MissionView, Role } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { MarkdownContent } from './MarkdownContent';
import { AGENT_ROLES, activityTitle, agentIsActive, agentWork, latestAgentTool } from './agentActivityModel';
import './agentActivity.css';

const ROLE_NAMES: Record<string, [string, string]> = {
  manager: ['统筹', 'Manager'], planner: ['规划', 'Planner'], engineer: ['执行', 'Engineer'], reviewer: ['审查', 'Reviewer'],
};
export function AgentActivity({ view, roles = [], events = [], taskId, paused = false, selectedRole,
  onSelectRole, onClose, showTabs = true }: {
  view?: MissionView | null; roles?: Role[]; events?: EventMsg[]; taskId?: string; paused?: boolean;
  selectedRole?: string; onSelectRole?: (role: string) => void; onClose?: () => void; showTabs?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [choice, setChoice] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now);
  const role = selectedRole || choice || roles.find((r) => r.active)?.role || view?.active_role || 'manager';
  const active = agentIsActive(view, roles, role, paused, taskId);
  const records = agentWork(view, role, taskId);
  const last = records[0];
  const update = records.find((row) => row.detail && ['agent_message', 'assistant_message', 'decision', 'verdict', 'handoff', 'review', 'completion'].includes(row.kind));
  const tool = latestAgentTool(events, role, taskId);
  const toolIsLatest = tool && Number(tool.ts || 0) >= (last?.ts ?? 0);
  const currentTitle = !toolIsLatest && last && ['agent_message', 'assistant_message'].includes(last.kind) && last.detail
    ? last.detail.split(/[。\n]/)[0].slice(0, 70)
    : activityTitle(toolIsLatest ? String(tool.kind) : last?.kind || 'task', zh, toolIsLatest ? String(tool.tool_name || '') : '');
  const model = roles.find((r) => r.role === role)?.model || view?.roles.find((r) => r.role === role)?.model;
  const seconds = last ? Math.max(0, Math.floor(now / 1000 - last.ts)) : 0;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  const name = (value: string) => ROLE_NAMES[value]?.[zh ? 0 : 1] || value;
  return <section className="agent-activity" aria-label={zh ? 'Agent 工作详情' : 'Agent work details'}>
    <header className="agent-activity-heading"><span><Activity size={15} />{zh ? 'Agent 动态' : 'Agent activity'}</span>
      {onClose && <button type="button" onClick={onClose} aria-label={zh ? '关闭 Agent 详情' : 'Close Agent details'}><X size={17} /></button>}
    </header>
    {showTabs && <div className="agent-activity-tabs" role="group" aria-label={zh ? '筛选 Agent' : 'Filter agents'}>
      {AGENT_ROLES.map((value) => <button type="button" key={value} data-role={value} aria-pressed={role === value}
        onClick={() => { setChoice(value); onSelectRole?.(value); }}>
        <i data-active={agentIsActive(view, roles, value, paused, taskId)} />{name(value)}
      </button>)}
    </div>}
    <div className="agent-current" data-active={active}>
      <div className="agent-current-kicker"><span>{active ? name(role) + (zh ? ' Agent 正在工作' : ' is working') : paused ? (zh ? '会话已暂停' : 'Session paused') : (zh ? '最近进度' : 'Latest progress')}</span>
        {active ? <span className="agent-live-indicator"><i />LIVE</span> : <Pause size={12} />}
      </div>
      <h3>{last || toolIsLatest ? currentTitle : (zh ? '等待任务分配' : 'Waiting for an assignment')}</h3>
      {update?.detail && <div className="agent-current-summary"><MarkdownContent>{update.detail}</MarkdownContent></div>}
      {!update && last?.detail && <p className="agent-current-summary">{last.detail}</p>}
      {last && <div className="agent-current-meta"><Clock3 size={12} /><span>{zh ? `${seconds < 60 ? seconds + ' 秒' : Math.floor(seconds / 60) + ' 分钟'}前更新` : `Updated ${seconds < 60 ? seconds + 's' : Math.floor(seconds / 60) + 'm'} ago`}</span>{model && <span>{model}</span>}</div>}
    </div>
    <div className="agent-records-heading"><span>{zh ? '工作记录' : 'Work log'}</span><span>{records.length} {zh ? '条' : 'records'}</span></div>
    <div className="agent-records" role="log" aria-live="off">
      {records.slice(0, 24).map((record, index) => {
        const running = active && index === 0;
        const done = ['done', 'completed'].includes(record.status);
        const failed = ['failed', 'error', 'rejected'].includes(record.status);
        const Icon = done ? Check : ['tool_use', 'command_execution'].includes(record.kind) ? Terminal : FileText;
        return <article className="agent-record" key={record.id} data-active={running} data-failed={failed}>
          <span className="agent-record-icon"><Icon size={13} /></span>
          <div><div className="agent-record-title"><strong>{activityTitle(record.kind, zh)}</strong><time>{new Date(record.ts * 1000).toLocaleTimeString(zh ? 'zh-CN' : 'en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}</time></div>
            <small>{running ? (zh ? '进行中' : 'In progress') : failed ? (zh ? '需要处理' : 'Needs attention') : done ? (zh ? '已完成' : 'Completed') : (zh ? '已记录' : 'Recorded')}{record.round_index != null ? (zh ? ` · 第 ${record.round_index} 轮` : ` · Round ${record.round_index}`) : ''}</small>
            {record.detail && <details open={index === 0 || record === update}>
              <summary><span>{zh ? '查看详情' : 'Read details'}</span><ChevronDown size={12} /></summary>
              <div className="agent-record-detail"><MarkdownContent>{record.detail}</MarkdownContent></div>
            </details>}
          </div>
        </article>;
      })}
      {!records.length && <p className="agent-records-empty">{zh ? `${name(role)}尚未留下这个任务的工作记录。` : `No work has been recorded for this task by ${name(role)}.`}</p>}
    </div>
  </section>;
}
