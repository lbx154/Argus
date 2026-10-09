import type { EventMsg, MissionView } from '../../../core/src/types';
import type { Locale } from '../i18n';
import { agentWork, activityTitle, latestAgentTool } from '../components/agentActivityModel';
import { shortWorkText } from './taskLanguage';
import { workStatusLabel, type WorkStatus } from './workStatus';

/** Prefer this task's recorded action over a generic role-at-work label. */
export function workSummary(status: WorkStatus, view: MissionView | null | undefined,
  events: EventMsg[], locale: Locale, connected = true, notBefore = 0): string {
  const fallback = workStatusLabel(status, locale, connected);
  if (!connected || status.state !== 'running' || status.foreground || !status.taskId || !status.role) return fallback;
  const started = Math.max(notBefore, view?.mission.id === status.taskId ? view.mission.started_at || 0 : 0);
  const rows = agentWork(view, status.role, status.taskId, started);
  const latest = rows.find(row => (row.detail && ['agent_message', 'assistant_message'].includes(row.kind))
    || ['tool_use', 'command_execution', 'file_change'].includes(row.kind));
  const tool = latestAgentTool(events, status.role, status.taskId, started);
  if (latest?.detail && ['agent_message', 'assistant_message'].includes(latest.kind)
    && (!tool || Number(tool.ts || 0) < latest.ts)) return shortWorkText(latest.detail, 150);
  if (tool && (!latest || Number(tool.ts || 0) >= latest.ts)) {
    const action = activityTitle(String(tool.kind), locale === 'zh-CN', String(tool.tool_name || ''));
    return status.title ? `${action}${locale === 'zh-CN' ? '：' : ': '}${shortWorkText(status.title, 70)}` : action;
  }
  if (latest && ['tool_use', 'command_execution', 'file_change'].includes(latest.kind)) {
    return activityTitle(latest.kind, locale === 'zh-CN');
  }
  return fallback;
}
