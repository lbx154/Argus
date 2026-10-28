import { describe, expect, it } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import type { Snapshot } from '../../../core/src/types';
import { activeProviderRequest, currentWorkStartedAt, currentWorkStatus, workStatusLabel } from '../lib/workStatus';

function fixture(alive = true): Snapshot {
  return {
    session: { id: 's-research', display_name: 'Research', objective: '', cwd: '/workspace', last_active: 100 },
    daemon: { alive, pid: alive ? 1 : null, uptime_seconds: 20, backend: 'pi'},
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
    expect(workStatusLabel(state, 'zh-CN')).toBe('正在检查结果是否正确');
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
    // Trial s-f0e1b2f0: the Planner closed the campaign and the sidebar still said "这一步已结束".
    snapshot.daemon.alive = false;
    snapshot.continuous = { enabled: false, objective: 'format_mib', done_reason: 'planner declared project done', done_at: '2026-10-22T10:33:15Z' };
    const done = currentWorkStatus(snapshot, view);
    expect(done).toMatchObject({ state: 'step_finished', reason: 'project_done' });
    expect(workStatusLabel(done, 'zh-CN')).toBe('项目已完成');
  });

  it('never calls a project done while its only result is a stage package with the overall goal open', () => {
    // Trial s-efe9deac: a reviewed plan (stage package, "整体目标未完成") and
    // then a clock-out stop; the header said "项目已完成".
    const snapshot = fixture(), view = emptyMissionView();
    view.mission.status = 'continued';
    view.routing.continuous = true;
    view.delivery = { kind: 'stage_package', status: 'done', item_id: '99d44fc3006a' } as unknown as typeof view.delivery;
    snapshot.backlog = [{ id: '99d44fc3006a', status: 'done', title: 'LLM 强化学习原创研究与 ACL 完整论文' }] as Snapshot['backlog'];
    view.mission.id = '99d44fc3006a';
    snapshot.continuous = { enabled: true, objective: 'ACL 论文' };
    const between = currentWorkStatus(snapshot, view);
    expect(between).toMatchObject({ state: 'waiting', reason: 'goal_open' });
    expect(workStatusLabel(between, 'zh-CN')).toBe('阶段成果已交付，正在安排下一阶段');
    snapshot.daemon.alive = false;
    snapshot.continuous = { enabled: false, objective: 'ACL 论文', done_reason: 'operator stop (graceful SIGTERM/SIGINT — clock out)', done_at: '2026-10-24T15:00:38Z' };
    const stopped = currentWorkStatus(snapshot, view);
    expect(stopped).toMatchObject({ state: 'step_finished', reason: 'goal_open' });
    expect(workStatusLabel(stopped, 'zh-CN')).toBe('阶段成果已交付，整体目标还没完成；发条消息就能让团队继续');
    expect(workStatusLabel(stopped, 'en')).toContain('the overall goal is still open');
    // The Planner judging the goal met is what closes the project.
    snapshot.continuous = { enabled: false, objective: 'ACL 论文', done_reason: 'planner declared project done', done_at: '2026-10-24T15:00:38Z' };
    expect(currentWorkStatus(snapshot, view).reason).toBe('project_done');
  });

  it('shows the Planner choosing the next step after a step finishes, but not post-step housekeeping', () => {
    const snapshot = fixture(), view = emptyMissionView();
    view.mission.status = 'complete';
    view.routing.continuous = true;
    const role = (name: string) => ({ role: name, active: true, backend: 'pi', backend_label: 'Pi', model: 'model', effort: 'high', label: 'working', status: 'running', age_s: 1 });
    snapshot.roles = [role('engineer')];
    expect(currentWorkStatus(snapshot, view).state).toBe('waiting');
    snapshot.roles = [role('planner')];
    const planning = currentWorkStatus(snapshot, view);
    expect(planning).toMatchObject({ state: 'running', role: 'planner' });
    expect(workStatusLabel(planning, 'zh-CN')).toBe('正在安排接下来要做的事');
    snapshot.daemon.alive = false;
    expect(currentWorkStatus(snapshot, view).state).not.toBe('running');
  });

  it('keeps paused work and unreadable status distinct from active work', () => {
    const snapshot = fixture(false), view = emptyMissionView();
    view.mission.status = 'parked';
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
    expect(workStatusLabel(state, 'zh-CN')).toBe('有个问题在等你回答');
    expect(workStatusLabel(state, 'en')).toBe('A question is waiting for your answer');
    expect(snapshot.daemon.alive).toBe(true);
    snapshot.backlog[1].pending_question = '';
    // Another task's question still waits on the operator.
    expect(currentWorkStatus(snapshot, view)).toMatchObject({ reason: 'operator_input', taskId: 'old', title: 'Old question' });
    snapshot.backlog[0].pending_question = '';
    expect(currentWorkStatus(snapshot, view).reason).toBe('not_running');
    expect(workStatusLabel(state, 'zh-CN', false)).toBe('实时连接已断开');
  });

  it('does not let a finished task hide another task still waiting for an answer', () => {
    // Trial s-934a4569: char_count finished while the truncate task waited on
    // the operator; every status line said "waiting for the next step".
    const snapshot = fixture(), view = emptyMissionView();
    Object.assign(view.mission, { id: 'count', status: 'complete' });
    view.routing.continuous = true;
    snapshot.backlog = [
      { id: 'truncate', title: 'strtools truncate', objective: '', status: 'parked', priority: 1, pending_question: 'A or B?' },
      { id: 'count', title: 'char_count', objective: '', status: 'done', priority: 1 },
    ];
    const state = currentWorkStatus(snapshot, view);
    expect(state).toMatchObject({ state: 'paused', reason: 'operator_input', taskId: 'truncate', title: 'strtools truncate' });
    expect(workStatusLabel(state, 'zh-CN')).toBe('有个问题在等你回答');
    snapshot.roles = [{ role: 'planner', active: true, backend: 'pi', backend_label: 'Pi', model: 'model', effort: 'high', label: 'working', status: 'running', age_s: 1 }];
    expect(currentWorkStatus(snapshot, view)).toMatchObject({ state: 'running', role: 'planner' });
    snapshot.roles = [];
    snapshot.backlog[0].pending_question = '';
    expect(currentWorkStatus(snapshot, view)).toMatchObject({ state: 'waiting', taskId: 'count' });
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
    expect(workStatusLabel(status, 'zh-CN')).toBe('模型服务暂时未能完成请求，等待后会重试');
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

describe('the Manager at work', () => {
  it('does not call the Manager’s own tidying “your request”', () => {
    const snapshot = fixture();
    snapshot.roles = [{ role: 'manager', active: true, backend: 'pi', backend_label: 'Pi', model: 'model', effort: 'high', label: 'summarizing', status: 'running', age_s: 1 }];
    const view = emptyMissionView();
    view.mission.status = 'working';
    view.active_role = 'manager';
    const status = currentWorkStatus(snapshot, view);
    expect(status).toMatchObject({ state: 'running', role: 'manager' });
    expect(status.foreground).toBeUndefined();
    expect(workStatusLabel(status, 'zh-CN')).toBe('正在整理任务进展');
    expect(workStatusLabel(status, 'en')).toBe('Summarizing progress');
  });
});

describe('how long ago something moved', () => {
  it('counts the Manager’s own turns and solo deliveries, not only team events', () => {
    // Hosted trial: the Manager answered and delivered files a minute ago,
    // yet the bar said "16 小时前有进展" from the last Engineer event.
    const now = 100_000;
    const snapshot = fixture(false), view = emptyMissionView();
    Object.assign(view.mission, { id: 'old', status: 'complete', started_at: now - 60_000 });
    snapshot.backlog = [{ id: 'old', title: 'Old', objective: '', status: 'done', priority: 1, started_ts: now - 60_000, finished_ts: now - 57_600 }];
    const team = { type: 'life.mission.completed', item_id: 'old', ts: now - 57_600 };
    expect(currentWorkStatus(snapshot, view, [team], now).activityAgeSeconds).toBe(57_600);
    const reply = {
      type: 'ui.argus', ts: now - 60, text: 'Delivered.', mission_result: true, success: true,
      steps: [{ kind: 'tool_use', label: 'write', status: 'completed', started_ts: now - 200, ended_ts: now - 70 }],
    };
    expect(currentWorkStatus(snapshot, view, [team, { type: 'ui.operator', ts: now - 300, text: 'Go' }, reply], now).activityAgeSeconds).toBe(60);
    // A turn still working counts by its newest step.
    const live = { type: 'ui.argus', ts: now - 400, live: true, in_progress: true, text: '', steps: [{ kind: 'tool_use', label: 'read', status: 'running', started_ts: now - 30, ended_ts: 0 }] };
    expect(currentWorkStatus(snapshot, view, [team, live], now).activityAgeSeconds).toBe(30);
  });
});
