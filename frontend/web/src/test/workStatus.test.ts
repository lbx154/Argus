import { describe, expect, it } from 'vitest';
import type { Snapshot } from '../../../core/src/types';
import { activeProviderRequest, currentWorkStatus, workStatusLabel } from '../lib/workStatus';

function fixture(alive = true): Snapshot {
  return {
    session: { id: 's-research', display_name: 'Research', objective: '', cwd: '/workspace', last_active: 100 },
    daemon: { alive, pid: alive ? 1 : null, uptime_seconds: 20, backend: 'pi'},
    roles: [], backlog: [], };
}

describe('server work status', () => {
  it('uses the server label across surfaces without inferring a conflicting state from tasks', () => {
    const snapshot = fixture(false);
    snapshot.work_status = { state: 'paused', reason: 'not_running', role: '', roles: [], taskId: '', title: '',
      activityAt: 150, foreground: false, label: { 'zh-CN': '已停止：2 个任务暂停', en: 'Stopped: 2 tasks paused' } };
    const status = currentWorkStatus(snapshot, 200);
    expect(status).toMatchObject({ state: 'paused', activityAgeSeconds: 50 });
    expect(workStatusLabel(status, 'zh-CN')).toBe('已停止：2 个任务暂停');
    expect(workStatusLabel(status, 'en')).toBe('Stopped: 2 tasks paused');
    expect(currentWorkStatus(undefined).state).toBe('unknown');
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
