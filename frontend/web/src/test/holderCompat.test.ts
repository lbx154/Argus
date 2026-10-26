import { describe, expect, it } from 'vitest';
import { missionOutcomePresentation, parkReason, settledStatus } from '../../../core/src/missionOutcome';

describe('statuses an older release wrote', () => {
  it('read a round status as the holder it named', () => {
    expect(settledStatus({ status: 'continue' })).toBe('engineer');
    expect(settledStatus({ status: 'replan_requested' })).toBe('planner');
    expect(settledStatus({ status: 'blocked' })).toBe('manager');
    expect(settledStatus({ status: 'blocked', operator_question: 'A or B?' })).toBe('operator');
    expect(settledStatus({ status: 'paused_no_breakthrough' })).toBe('planner');
    for (const holder of ['engineer', 'planner', 'manager', 'operator', 'done']) {
      expect(settledStatus({ status: holder })).toBe(holder);
    }
  });

  it('read each paused_* status as one park with its reason', () => {
    expect(parkReason('paused_budget')).toBe('budget_exhausted');
    expect(parkReason('paused_operator')).toBe('operator_pause');
    expect(parkReason('parked', 'provider_fence')).toBe('provider_fence');
    expect(parkReason('running')).toBeNull();
  });
});

describe('a task handed past the team', () => {
  it('waits on the operator or the Manager and never reads as failed', () => {
    const asked = missionOutcomePresentation({
      type: 'life.mission.completed', status: 'operator', success: false, operator_question: 'A or B?',
    } as never);
    expect(asked.kind).toBe('mission_paused');
    const handed = missionOutcomePresentation({ type: 'life.mission.completed', status: 'manager', success: false } as never);
    expect(handed.outcomeClass).not.toBe('failed');
    expect(handed.tone).not.toBe('err');
  });
});
