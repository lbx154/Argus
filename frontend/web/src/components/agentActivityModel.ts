import type { EventMsg, MissionRoleWorkItem, MissionView, Role } from '../../../core/src/types';
import { visibleAgentText } from '../../../core/src/events';

export { AGENT_ROLES } from '../lib/agentRoles';
const PUBLIC_KINDS = new Set(['grounding', 'task', 'decision', 'agent_message', 'assistant_message', 'command_execution', 'tool_use', 'handoff', 'review', 'verdict', 'completion', 'plan', 'file_change', 'result']);
const GENERIC = /^(using a tool|running project command|inspecting project state|working|reporting progress|暂无详细记录)$/i;
export function cleanActivityText(value: string): string {
  const clean = visibleAgentText(value).replace(/^\s*(?:RESULT|SUMMARY)\s*=\s*/gim, '').trim();
  return GENERIC.test(clean) || clean.startsWith('{') ? '' : clean;
}
export function activityTitle(kind: string, zh: boolean, tool = ''): string {
  if (tool) {
    if (/^(?:rg|grep|glob|search)$/.test(tool)) return zh ? '检索项目文件' : 'Searching project files';
    if (/^(?:view|read|read_file)$/.test(tool)) return zh ? '读取文件' : 'Reading a file';
    if (/apply_patch|edit|write/.test(tool)) return zh ? '编辑文件' : 'Editing a file';
    if (/bash|shell|exec|terminal/.test(tool)) return zh ? '运行终端命令' : 'Running a command';
    if (/playwright|browser/.test(tool)) return zh ? '检查浏览器交互' : 'Checking browser interactions';
  }
  const titles: Record<string, [string, string]> = {
    grounding: ['理解任务与检查项目', 'Understanding the task'], task: ['安排任务', 'Task assignment'],
    decision: ['确定执行方案', 'Execution decision'], plan: ['制定执行计划', 'Planning the work'],
    agent_message: ['更新执行进度', 'Progress update'], assistant_message: ['更新执行进度', 'Progress update'],
    command_execution: ['运行项目命令', 'Running a project command'], tool_use: ['使用工具', 'Using a tool'],
    file_change: ['更新项目文件', 'Updating project files'], handoff: ['提交结果与交接', 'Results and handoff'],
    review: ['复核实现与结果', 'Reviewing implementation and results'], verdict: ['给出审查结论', 'Review verdict'],
    completion: ['完成任务', 'Task completed'], result: ['产出结果', 'Result'],
  };
  return (titles[kind] ?? [kind, kind])[zh ? 0 : 1];
}
export function agentWork(view: MissionView | null | undefined, role: string, taskId?: string): MissionRoleWorkItem[] {
  const unique = new Map<string, MissionRoleWorkItem>();
  for (const row of view?.role_work ?? []) {
    if (row.role !== role || !PUBLIC_KINDS.has(row.kind)) continue;
    const owner = row.item_id || row.mission_id;
    if (taskId && owner !== taskId) continue;
    unique.set(row.id, { ...row, detail: cleanActivityText(row.detail) });
  }
  return [...unique.values()].sort((a, b) => b.ts - a.ts);
}
export function latestAgentTool(events: EventMsg[], role: string, taskId?: string) {
  return [...events].reverse().find((event) => event.type === 'engineer.progress'
    && ['tool_use', 'command_execution', 'file_change'].includes(String(event.kind))
    && String(event.agent_layer || event.actor || event.role) === role
    && (!taskId || String(event.item_id || event.mission_id) === taskId));
}
export function agentIsActive(view: MissionView | null | undefined, roles: Role[], role: string, paused: boolean, taskId?: string) {
  if (paused || ['done', 'completed', 'failed', 'aborted', 'stopped'].includes(view?.mission.status ?? '')) return false;
  if (taskId && view?.mission.id !== taskId) return false;
  if (roles.length) return roles.some((r) => r.role === role && r.active);
  return view?.active_role === role && ['working', 'grounding', 'framed', 'running'].includes(view?.mission.status ?? '');
}
