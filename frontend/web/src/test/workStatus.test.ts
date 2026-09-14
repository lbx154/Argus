import { describe, expect, it } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { Snapshot } from '../../../core/src/types';
import { activeProviderRequest, currentWorkStartedAt, currentWorkStatus, workStatusLabel } from '../lib/workStatus';

function fixture(alive = true): Snapshot {
  return {
    session: { id: 's-research', display_name: 'Research', objective: '', cwd: '/workspace', last_active: 100 },
    daemon: { alive, pid: alive ? 1 : null, uptime_seconds: 20, backend: 'pi', global_daily_cap_usd: null },
    roles: [], backlog: [], recent_events: [],
  };
}

describe('shared work status', () => {
  it('shares the recorded attempt boundary without applying another task’s start', () => {
    const snapshot = fixture(), view = emptyMissionView();
    Object.assign(view.mission, { id: 'current', started_at: 200 });
    snapshot.backlog = [{ id: 'current', title: 'Current', objective: '', status: 'running', priority: 1, started_ts: 210 }];
    expect(currentWorkStartedAt(snapshot, view, 'current')).toBe(210);
    snapshot.daemon.started_at_iso = '1970-01-01T00:04:00Z';
    expect(currentWorkStartedAt(snapshot, view, 'current')).toBe(240);
    expect(currentWorkStartedAt(undefined, view, 'current')).toBe(200);
    expect(currentWorkStartedAt(undefined, view, 'history')).toBe(0);
    view.mission.started_at = null;
    expect(currentWorkStartedAt(undefined, view, 'current')).toBe(0);
  });

  it('uses a live role rather than daemon existence to claim active work', () => {
    const snapshot = fixture();
    const view = emptyMissionView();
    view.mission.status = 'working';
    view.active_role = 'engineer';
    expect(currentWorkStatus(snapshot, view).state).toBe('waiting');
    snapshot.roles = [{ role: 'reviewer', active: true, backend: 'pi', backend_label: 'Pi', model: 'model', effort: 'high', label: 'using a tool', status: 'running', age_s: 1 }];
    const state = currentWorkStatus(snapshot, view);
    expect(state).toMatchObject({ state: 'running', role: 'reviewer' });
    expect(workStatusLabel(state, 'zh-CN')).toBe('正在核对这一步的结果');
  });

  it('tracks foreground SELF work and its step-less durable delivery without classifying chat or queue receipts', () => {
    const snapshot = fixture(false);
    snapshot.manager_requests = [{ request_id: 'self-request', status: 'running' }];
    const running = currentWorkStatus(snapshot, null, [
      { type: 'agent.io.start', call_id: 'self-call', run_label: 'self-implement', ts: 190 },
    ], 200);
    expect(running).toMatchObject({ state: 'running', role: 'manager', activityAt: 190 });
    expect(workStatusLabel(running, 'zh-CN')).toBe('正在处理你的请求');

    snapshot.manager_requests = [];
    const completed = currentWorkStatus(snapshot, null, [{
      type: 'ui.argus', ts: 210, text: 'Implemented.', mission_result: true, success: true, steps: [],
    }], 220);
    expect(completed).toMatchObject({ state: 'step_finished', activityAt: 210 });
    expect(workStatusLabel(completed, 'zh-CN')).toBe('这一步已结束');
    expect(currentWorkStatus(snapshot, null, [
      { type: 'ui.argus', ts: 211, text: 'Hello.', success: true, steps: [] },
    ], 220).state).toBe('idle');
    expect(currentWorkStatus(snapshot, null, [
      { type: 'ui.argus', ts: 212, text: 'Queued.', mission_result: true, item_id: 'queued-task', success: true },
    ], 220).state).toBe('idle');
  });

  it('does not treat a completed step in continuous research as the whole project finishing', () => {
    const snapshot = fixture(), view = emptyMissionView();
    view.mission.status = 'complete';
    view.routing.continuous = true;
    expect(currentWorkStatus(snapshot, view).state).toBe('waiting');
    view.routing.continuous = false;
    expect(workStatusLabel(currentWorkStatus(snapshot, view), 'zh-CN')).toBe('这一步已结束');
  });

  it('keeps paused work and unreadable status distinct from active work', () => {
    const snapshot = fixture(false), view = emptyMissionView();
    view.mission.status = 'paused_external_work';
    expect(currentWorkStatus(snapshot, view).state).toBe('paused');
    snapshot.daemon.read_status = 'error';
    expect(currentWorkStatus(snapshot, view).state).toBe('unknown');
  });

  it('identifies the current paused task waiting for an answer without stopping the daemon', () => {
    const snapshot = fixture(), view = emptyMissionView();
    view.mission.id = 'current';
    view.mission.status = 'blocked';
    snapshot.backlog = [
      { id: 'old', title: 'Old question', objective: '', status: 'paused_operator', priority: 1, pending_question: 'Recorded provider error' },
      { id: 'current', title: 'Author details', objective: '', status: 'paused_operator', priority: 1, pending_question: 'Provide the author details' },
    ];
    const state = currentWorkStatus(snapshot, view);
    expect(state).toMatchObject({ state: 'paused', taskId: 'current', reason: 'operator_input' });
    expect(workStatusLabel(state, 'zh-CN')).toBe('当前任务等待你的回复');
    expect(workStatusLabel(state, 'en')).toBe('This task is waiting for your reply');
    expect(snapshot.daemon.alive).toBe(true);
    snapshot.backlog[1].pending_question = '';
    expect(currentWorkStatus(snapshot, view).reason).toBe('not_running');
    expect(workStatusLabel(state, 'zh-CN', false)).toBe('实时连接已断开');
  });

  it('does not turn repeated planner waiting notices into fresh research progress', () => {
    const snapshot = fixture(), view = emptyMissionView();
    Object.assign(view.mission, { id: 'current', status: 'blocked', started_at: 100 });
    view.role_work = [
      { id: 'actual-work', item_id: 'current', ts: 110, role: 'engineer', kind: 'tool_use', title: 'Read source', detail: '', status: 'done' },
      { id: 'waiting', mission_id: 'current', ts: 195, role: 'planner', kind: 'waiting', title: 'Waiting for author facts', detail: '', status: 'waiting' },
      { id: 'idle', mission_id: 'current', ts: 198, role: 'planner', kind: 'planning', title: 'Idle', detail: '', status: 'idle' },
    ];
    expect(currentWorkStatus(snapshot, view, [{ type: 'life.planner.waiting', item_id: 'current', ts: 199 }], 200))
      .toMatchObject({ activityAt: 110, activityAgeSeconds: 90 });
    view.role_work = view.role_work.slice(1);
    expect(currentWorkStatus(snapshot, view, [], 200).activityAt).toBeNull();
  });

  it('does not use other tasks or old attempts as fresh progress for the current step', () => {
    const view = emptyMissionView();
    Object.assign(view.mission, { id: 'current', started_at: 100, status: 'working' });
    const status = currentWorkStatus(fixture(), view, [
      { type: 'engineer.progress', item_id: 'current', ts: 90 },
      { type: 'engineer.progress', item_id: 'current', ts: 110 },
      { type: 'engineer.progress', item_id: 'other', ts: 130 },
      { type: 'engineer.progress', ts: 135 },
    ], 140);
    expect(status.activityAt).toBe(110);
    expect(status.activityAgeSeconds).toBe(30);
  });

  it('reports recorded retry waiting without inventing a failed proof', () => {
    const view = emptyMissionView();
    view.mission.id = 'task';
    const status = currentWorkStatus(fixture(), view, [
      { type: 'round.backend_failure.backoff', item_id: 'task', ts: 100, seconds: 60 },
    ], 120);
    expect(status).toMatchObject({ state: 'waiting', reason: 'provider_wait' });
    expect(workStatusLabel(status, 'zh-CN')).toBe('模型服务等待后重试');
  });

  it('does not assign a different task’s live worker to a paused task', () => {
    const snapshot = fixture(), view = emptyMissionView();
    Object.assign(view.mission, { id: 'a', title: 'Task A', status: 'working', started_at: 100 });
    snapshot.backlog = [
      { id: 'a', title: 'Task A', objective: '', status: 'paused_operator', priority: 1 },
      { id: 'b', title: 'Task B', objective: '', status: 'running', priority: 1 },
    ];
    snapshot.roles = [{ role: 'engineer', active: true, backend: 'pi', backend_label: 'Pi', model: 'model', effort: 'high', label: 'working', status: 'running', age_s: 1 }];
    expect(currentWorkStatus(snapshot, view, [], 300)).toMatchObject({ taskId: 'a', state: 'paused', role: '' });
    snapshot.backlog[0].status = 'running';
    expect(currentWorkStatus(snapshot, view, [], 300).state).toBe('unknown');
    view.role_work = [{ id: 'a-work', item_id: 'a', ts: 250, role: 'engineer', kind: 'tool_use', title: 'Reading', detail: '', status: 'active' }];
    expect(currentWorkStatus(snapshot, view, [], 300)).toMatchObject({ state: 'running', role: 'engineer' });
  });

  it('does not reuse a previous attempt’s or an unassociated retry wait', () => {
    const snapshot = fixture(), view = emptyMissionView();
    Object.assign(view.mission, { id: 'a', status: 'working', started_at: 200 });
    for (const event of [
      { type: 'round.backend_failure.backoff', item_id: 'a', ts: 110, seconds: 960 },
      { type: 'round.backend_failure.backoff', ts: 210, seconds: 960 },
    ]) expect(currentWorkStatus(snapshot, view, [event], 220).reason).not.toBe('provider_wait');
  });
});

describe('call status across concurrent tasks', () => {
  it('closes only the completed task’s calls and keeps Pi calls visible until their own completion', () => {
    const a = { type: 'agent.io.start', call_id: 'a', mission_id: 'task-a:attempt:1', ts: 100 };
    const b = { type: 'provider.request.started', call_id: 'b', item_id: 'task-b', ts: 101 };
    expect(activeProviderRequest([a, b, { type: 'life.mission.completed', item_id: 'task-b', ts: 102 }])).toEqual(a);
    expect(activeProviderRequest([a, { type: 'agent.io.complete', call_id: 'a', ts: 103 }])).toBeNull();
  });

  it('does not display old-run or narration calls as ongoing research', () => {
    expect(activeProviderRequest([
      { type: 'agent.io.start', call_id: 'old', ts: 90 },
      { type: 'agent.io.start', call_id: 'reading', run_label: 'map-summary', ts: 110 },
    ], 100)).toBeNull();
  });
});
