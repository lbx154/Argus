import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { MissionRoleWorkItem, Role } from '../../../core/src/types';
import { AgentActivity } from '../components/AgentActivity';
import { activityTitle, agentIsActive, agentWork, cleanActivityText, latestAgentTool } from '../components/agentActivity';
const record = (id: string, kind: string, detail: string, task = 'current'): MissionRoleWorkItem => ({
  id, role: 'engineer', kind, detail, title: kind, status: 'active', ts: 100, item_id: task,
});
const role: Role = { role: 'engineer', backend: 'copilot', backend_label: 'Copilot', model: 'example-model', effort: 'high', label: 'working', status: 'running', active: true, age_s: 1 };
describe('Agent activity', () => {
  it('scopes details to a task and excludes reasoning and machine output', () => {
    const view = emptyMissionView();
    view.role_work = [record('one', 'agent_message', 'Checking 500 weighted maps.'), record('two', 'reasoning', 'private scratchpad'), record('three', 'agent_message', 'Other task', 'another'), record('four', 'agent_message', '{"verdict":"done"}')];
    const rows = agentWork(view, 'engineer', 'current');
    expect(rows.map((r) => r.id)).toEqual(['one', 'four']);
    expect(rows.map((r) => r.detail).join(' ')).toBe('Checking 500 weighted maps. ');
    expect(cleanActivityText('Useful result.\nDecision:\nMILESTONE_STATUS=done\nNEXT_OWNER=reviewer')).toBe('Useful result.');
  });
  it('does not present old records or paused sessions as active work', () => {
    const view = emptyMissionView(); view.mission.id = 'current'; view.mission.status = 'working'; view.active_role = 'engineer';
    expect(agentIsActive(view, [role], 'engineer', false, 'current')).toBe(true);
    expect(agentIsActive(view, [role], 'engineer', true, 'current')).toBe(false);
    expect(agentIsActive(view, [role], 'engineer', false, 'old')).toBe(false);
    expect(agentIsActive(view, [{ ...role, active: false }], 'engineer', false, 'current')).toBe(false);
  });
  it('uses tool metadata without rendering raw commands or another task', () => {
    const events = [
      { type: 'engineer.progress', kind: 'tool_use', agent_layer: 'engineer', item_id: 'current', tool_name: 'rg', text: 'private command arguments' },
      { type: 'engineer.progress', kind: 'tool_use', agent_layer: 'engineer', item_id: 'other', tool_name: 'bash' },
      { type: 'engineer.progress', kind: 'reasoning', agent_layer: 'engineer', item_id: 'current', tool_name: 'bash' },
    ];
    expect(latestAgentTool(events, 'engineer', 'current')?.tool_name).toBe('rg');
    expect(activityTitle('tool_use', true, 'rg')).toBe('检索项目文件');
  });
  it('shows a concrete progress update and role selection', () => {
    const view = emptyMissionView(); view.mission.id = 'current';
    view.role_work = [record('one', 'agent_message', 'Checking 500 weighted maps.')];
    const html = renderToStaticMarkup(<AgentActivity view={view} roles={[role]} taskId="current" selectedRole="engineer" />);
    expect(html).toContain('Checking 500 weighted maps.'); expect(html).toContain('example-model');
    expect(html).toContain('aria-pressed="true"'); expect(html).not.toContain('private scratchpad');
  });
});
