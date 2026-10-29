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
});

const role = (name: string, active: boolean, last?: number) => ({
  role: name, active, backend: 'codex', backend_label: 'Codex', model: 'gpt', effort: null, label: name,
  status: active ? 'running' : 'idle', age_s: null, last: last ? { text: `${name} said`, ts: last } : null,
});
const task = (id: string, status: string) => ({ id, title: `Task ${id}`, objective: '', status, priority: 0 });
const question = (id: string) => ({ id, title: 'The Reviewer asks', status: 'blocked', operator_decision: {
  id: `q-${id}`, item_id: id, revision: 1, status: 'pending', title: 'Ship it?', reason: '', question: 'Ship it as is?',
  evidence: [], options_source: 'agent', selected_option: '', note: '', options: [] } });

describe('work status, as the server says it', () => {
  it('is the Manager answering while a reply of the operator\'s is under way', () => {
    const snapshot = { ...fixture(false), manager_requests: [{ request_id: 'r1', status: 'running' as const }] };
    const status = currentWorkStatus(snapshot);
    expect(status).toMatchObject({ state: 'running', role: 'manager', foreground: true });
    expect(workStatusLabel(status, 'zh-CN')).toBe('正在处理你的请求');
  });

  it('is running while any role is at work, and names every role at work', () => {
    // Trial screenshots 01-d-first and 01-m-first: three tasks side by side read "这一步已结束"
    // while the Reviewer was at work on one of them.
    const snapshot = fixture();
    snapshot.backlog = [task('a', 'done'), task('b', 'done'), task('c', 'in_progress')];
    snapshot.roles = [role('manager', false), role('reviewer', true)];
    const one = currentWorkStatus(snapshot);
    expect(one).toMatchObject({ state: 'running', role: 'reviewer', roles: ['reviewer'], taskId: 'c', title: 'Task c' });
    expect(workStatusLabel(one, 'zh-CN')).toBe('正在检查结果：Task c');
    snapshot.backlog = [task('a', 'in_progress'), task('b', 'in_progress')];
    snapshot.roles = [role('engineer', true), role('reviewer', true)];
    const two = currentWorkStatus(snapshot);
    expect(two).toMatchObject({ state: 'running', roles: ['engineer', 'reviewer'], taskId: '', title: '' });
    expect(workStatusLabel(two, 'zh-CN')).toBe('工程师、审阅者正在工作');
    expect(workStatusLabel(two, 'en')).toBe('Engineer, Reviewer at work');
    // One role on both tasks is not "the current task".
    snapshot.roles = [role('engineer', true)];
    expect(workStatusLabel(currentWorkStatus(snapshot), 'zh-CN')).toBe('工程师正在工作');
  });

  it('waits for the operator when a question is open and no role is at work', () => {
    const snapshot = fixture(false);
    snapshot.backlog = [task('t1', 'blocked')];
    snapshot.pending_questions = [question('t1')];
    const status = currentWorkStatus(snapshot);
    expect(status).toMatchObject({ state: 'paused', reason: 'operator_input', taskId: 't1' });
    expect(workStatusLabel(status, 'zh-CN')).toBe('有个问题在等你回答');
    snapshot.roles = [role('engineer', true)];
    expect(currentWorkStatus(snapshot).state).toBe('running');
  });

  it('waits for the next step while the run goes on with no role at work', () => {
    const status = currentWorkStatus(fixture(true));
    expect(status).toMatchObject({ state: 'waiting', reason: 'awaiting_next_step' });
    expect(workStatusLabel(status, 'zh-CN')).toBe('等待下一步工作');
  });

  it('with no run, is stopped while a task is not done, failed ones included, and idle once all are', () => {
    const snapshot = fixture(false);
    for (const unfinished of ['pending', 'in_progress', 'blocked', 'failed']) {
      snapshot.backlog = [task('a', 'done'), task('b', unfinished)];
      expect(currentWorkStatus(snapshot)).toMatchObject({ state: 'paused', reason: 'not_running' });
    }
    snapshot.backlog = [task('a', 'done')];
    expect(currentWorkStatus(snapshot)).toMatchObject({ state: 'idle', reason: '' });
    expect(currentWorkStatus(undefined).state).toBe('unknown');
  });

  it('dates the last thing that moved, from the record and the roles\' last words', () => {
    const snapshot = fixture();
    snapshot.roles = [role('reviewer', false, 150)];
    expect(currentWorkStatus(snapshot, null, [{ type: 'engineer.progress', agent_layer: 'engineer', ts: 120 }], 200))
      .toMatchObject({ activityAt: 150, activityAgeSeconds: 50 });
    // The operator writing is not the work moving.
    expect(currentWorkStatus(snapshot, null, [{ type: 'ui.operator', agent_layer: 'operator', ts: 190 }], 200).activityAt).toBe(150);
    expect(currentWorkStatus(fixture(), null, [], 200).activityAt).toBeNull();
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
