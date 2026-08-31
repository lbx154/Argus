import { describe, expect, it } from 'vitest';
import { deriveProgressEstimate } from './progressEstimate';
import type { EventMsg, Snapshot } from './types';

function snapshot(overrides: { alive?: boolean; activeStatus?: string; reviewStatus?: string; complete?: boolean } = {}): Snapshot {
  const complete = overrides.complete ?? false;
  return {
    session: { id: 's-test', display_name: 'Test', objective: '', last_active: 1_000, cwd: '/tmp', workdir: '/tmp' },
    daemon: { alive: overrides.alive ?? true, pid: 1, uptime_seconds: 500, backend: 'pi', health: { state: overrides.alive === false ? 'stopped' : 'active', seconds_since_progress: 2 } },
    roles: [
      { role: 'planner', backend: 'pi', backend_label: 'Pi', model: 'm', effort: null, active: false, label: 'done', status: 'done', age_s: 1 },
      { role: 'engineer', backend: 'pi', backend_label: 'Pi', model: 'm', effort: null, active: !complete, label: 'running command', status: complete ? 'done' : 'running', age_s: 1 },
    ],
    backlog: [
      { id: 'done', title: 'Completed task', objective: '', status: 'done', priority: 1, started_ts: 100, finished_ts: 300 },
      { id: 'active', title: 'Current task', objective: 'Run probe', status: complete ? 'done' : overrides.activeStatus ?? 'running', priority: 2, started_ts: 900, finished_ts: complete ? 990 : null },
    ],
    recent_events: [],
    mission_view: {
      schema_version: 4,
      mission: { id: 'active', title: 'Current task', objective: 'Run probe', status: complete ? 'complete' : 'working', started_at: 900, completed_at: complete ? 990 : null, elapsed_seconds: 100, campaign_started_at: 100, campaign_elapsed_seconds: 900 },
      stage: { id: 'experiment', label: 'Experiment' }, round: { current: 1, max: 3 }, active_role: complete ? '' : 'engineer', roles: [], role_work: [],
      dag: [
        { id: 'done', title: 'Completed task', objective: '', status: 'done', deps: [], branch_id: 'done' },
        { id: 'active', title: 'Current task', objective: 'Run probe', status: complete ? 'done' : overrides.activeStatus ?? 'running', deps: ['done'], branch_id: 'active' },
      ],
      timeline: [], artifacts: [], learned_skills: [], learned_wiki_pages: [], achievement: null,
      review: { status: overrides.reviewStatus ?? '', reason: '', rejected_attempts: 0 }, frontier: { change: '', summary: '', updated_at: 0 }, outcome: {}, last_event_ts: 0, updated_at: 1_000,
    },
  } as unknown as Snapshot;
}

const events: EventMsg[] = [
  { type: 'engineer.progress', ts: 850, kind: 'command_execution', text: 'old command', action_summary: 'old step' },
  { type: 'engineer.progress', ts: 950, kind: 'command_execution', text: 'python run_probe.py', action_summary: 'running probe' },
];

describe('deriveProgressEstimate', () => {
  it('uses only events after the current mission start', () => {
    const result = deriveProgressEstimate(snapshot(), events, 1_000);
    expect(result.currentStep).toBe('running probe');
    expect(result.currentDetail).toContain('run_probe.py');
    expect(result.estimate).toBeGreaterThan(result.confirmed);
    expect(result.eta).not.toBeNull();
  });

  it('does not claim current execution or ETA when the daemon is stopped', () => {
    const result = deriveProgressEstimate(snapshot({ alive: false }), events, 1_000);
    expect(result.currentRole).toBe('stopped');
    expect(result.currentStep).toMatch(/^已停止/);
    expect(result.eta).toBeNull();
    expect(result.etaUnavailableReason).toContain('Argus 已停止');
    expect(result.elapsedSeconds).toBe(50);
    expect(deriveProgressEstimate(snapshot({ alive: false }), events, 2_000).elapsedSeconds).toBe(50);
    expect(result.checkpoints.every((checkpoint) => checkpoint.status !== 'active')).toBe(true);
  });

  it('reports exactly complete with no remaining ETA', () => {
    const result = deriveProgressEstimate(snapshot({ complete: true }), events, 1_000);
    expect(result.confirmed).toBe(1);
    expect(result.estimate).toBe(1);
    expect(result.range).toEqual([1, 1]);
    expect(result.currentStep).toBe('项目已完成');
    expect(result.eta).toBeNull();
    expect(result.etaUnavailableReason).toContain('已完成');
    expect(result.elapsedSeconds).toBe(90);
    expect(result.checkpoints.every((checkpoint) => checkpoint.status === 'done')).toBe(true);
  });

  it('does not treat incomplete as a completed mission', () => {
    const value = snapshot();
    value.mission_view!.mission.status = 'incomplete';
    value.backlog.forEach((item) => { item.status = 'done'; });
    value.mission_view!.dag.forEach((item) => { item.status = 'done'; });
    const result = deriveProgressEstimate(value, events, 1_000);
    expect(result.confirmed).toBeLessThan(1);
    expect(result.currentStep).not.toBe('项目已完成');
  });

  it('suppresses ETA while Reviewer is replanning', () => {
    const result = deriveProgressEstimate(snapshot({ reviewStatus: 'replan_requested' }), events, 1_000);
    expect(result.eta).toBeNull();
    expect(result.etaUnavailableReason).toContain('Reviewer');
    expect(result.estimate).toBe(result.confirmed);
    expect(result.range).toEqual([result.confirmed, result.confirmed]);
    expect(result.currentFraction).toBe(0);
    expect(result.checkpoints.every((checkpoint) => checkpoint.status !== 'active')).toBe(true);
  });

  it('localizes generated progress labels without changing project content', () => {
    const result = deriveProgressEstimate(snapshot({ complete: true }), events, 1_000, 'en');
    expect(result.currentStep).toBe('Project complete');
    expect(result.etaUnavailableReason).toContain('Project complete');
    expect(result.checkpoints.map((checkpoint) => checkpoint.label)).toContain('Reviewer certification');
    expect(result.currentTask).toBe('Current task');
  });
});
