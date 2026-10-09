import { describe, expect, it } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { MissionRoleWorkItem } from '../../../core/src/types';
import { workSummary } from '../lib/workSummary';
import type { WorkStatus } from '../lib/workStatus';

const status: WorkStatus = { state: 'running', role: 'engineer', taskId: 'login', title: '修复登录错误',
  activityAt: 210, activityAgeSeconds: 0, reason: '' };
const record = (id: string, detail: string, ts = 210, item_id = 'login'): MissionRoleWorkItem => ({
  id, detail, ts, item_id, role: 'engineer', title: '', kind: 'agent_message', status: 'active',
});

describe('human task progress', () => {
  it('shows the current task action and hides private reasoning, JSON and another task', () => {
    const view = emptyMissionView();
    Object.assign(view.mission, { id: 'login', started_at: 200 });
    view.role_work = [record('progress', '正在检查登录参数，确认哪里传错了。'),
      record('other', '不相关的任务进展', 250, 'other'),
      { ...record('reasoning', '内部推理', 260), kind: 'reasoning' },
      record('receipt', '{"verdict":"done"}', 270)];
    expect(workSummary(status, view, [], 'zh-CN')).toBe('正在检查登录参数，确认哪里传错了。');
  });

  it('does not reuse a previous attempt or claim success from a failed result', () => {
    const view = emptyMissionView();
    Object.assign(view.mission, { id: 'login', started_at: 200 });
    view.role_work = [record('old', '已修复全部问题。', 100)];
    expect(workSummary(status, view, [], 'zh-CN')).toBe('正在处理：修复登录错误');
    expect(workSummary({ ...status, state: 'step_finished', reason: 'task_failed' }, view, [], 'zh-CN'))
      .toBe('这一步执行未完成');
  });

  it('uses an explicit boot boundary even when the view has no start time', () => {
    const view = emptyMissionView();
    view.mission.id = 'login';
    view.role_work = [record('old', '上一次运行的进展。', 100)];
    expect(workSummary(status, view, [], 'zh-CN', true, 200)).toBe('正在处理：修复登录错误');
  });

  it('describes the newest tool without exposing its command arguments', () => {
    const view = emptyMissionView();
    Object.assign(view.mission, { id: 'login', started_at: 200 });
    view.role_work = [record('progress', '正在查找原因。')];
    const events = [{ type: 'engineer.progress', kind: 'tool_use', tool_name: 'rg',
      agent_layer: 'engineer', item_id: 'login', ts: 220, text: 'rg private-token --debug' }];
    const text = workSummary(status, view, events, 'zh-CN');
    expect(text).toBe('检索项目文件：修复登录错误');
    expect(text).not.toContain('private-token');
  });

  it('reports waiting, user input and a lost connection without inventing activity', () => {
    expect(workSummary({ ...status, state: 'paused', reason: 'operator_input' }, null, [], 'zh-CN'))
      .toBe('当前任务等待你的回复');
    expect(workSummary({ ...status, state: 'waiting', reason: 'no_recent_progress' }, null, [], 'zh-CN'))
      .toBe('暂时没有收到新的进展');
    expect(workSummary(status, null, [], 'zh-CN', false)).toBe('实时连接已断开');
  });
});
