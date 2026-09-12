import { describe, expect, it } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { Snapshot } from '../../../core/src/types';
import { activeProviderRequest, currentWorkStatus, workStatusLabel } from '../lib/workStatus';

function fixture(alive = true): Snapshot {
  return {
    session: { id: 's-research', display_name: 'Research', objective: '', cwd: '/workspace', last_active: 100 },
    daemon: { alive, pid: alive ? 1 : null, uptime_seconds: 20, backend: 'pi', global_daily_cap_usd: null },
    roles: [], backlog: [], recent_events: [],
  };
}

describe('shared work status', () => {
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
