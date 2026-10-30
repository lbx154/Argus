import { describe, expect, it } from 'vitest';
import { renderLine } from '../../../core/src/eventRender';
import { missionOutcomePresentation, parkReason, settledStatus } from '../../../core/src/missionOutcome';

describe('statuses an older release wrote', () => {
  it('read a round status as the holder it named', () => {
    expect(settledStatus({ status: 'continue' })).toBe('engineer');
    expect(settledStatus({ status: 'replan_requested' })).toBe('planner');
    expect(settledStatus({ status: 'blocked', operator_question: 'A or B?' })).toBe('operator');
    for (const holder of ['engineer', 'planner', 'manager', 'operator', 'done']) {
      expect(settledStatus({ status: holder })).toBe(holder);
    }
  });

  it('read a status that names no holder as itself', () => {
    // A host stop or a refused task hands the work to nobody; an old research
    // verdict is a park, not a request for a new plan.
    for (const status of ['blocked', 'paused_no_breakthrough', 'exhausted_current_methods', 'research_incomplete']) {
      expect(settledStatus({ status })).toBe(status);
    }
  });

  it('read each paused_* status as one park with its reason', () => {
    expect(parkReason('paused_budget')).toBe('budget_exhausted');
    expect(parkReason('paused_operator')).toBe('operator_pause');
    expect(parkReason('parked', 'provider_fence')).toBe('provider_fence');
    expect(parkReason('running')).toBeNull();
    expect(parkReason('operator')).toBe('operator_question');
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

describe('the feed line for a review', () => {
  it('says a Reviewer handing the work to the Planner asked for a new plan, not another pass', () => {
    const text = renderLine({ type: 'round.review.completed', status: 'planner', reason: 'Split it.' } as never, {
      locale: 'en', showReasoning: true, unknownEventPolicy: 'hide', density: 'compact',
    })!.text;
    expect(text).toBe('the Reviewer asked the Planner for a new plan · Split it.');
  });
});

describe('a task the trial allowance paused', () => {
  it('says the allowance ran out, not that the model service refused calls', () => {
    const paused = missionOutcomePresentation({
      type: 'life.mission.completed', status: 'parked', park_reason: 'provider_fence', stop_kind: 'provider_fence',
      success: false, resumable: true, outcome_class: 'ended', failure_kind: 'allowance_exhausted',
    } as never, 'zh-CN');
    expect(paused.label).toBe('任务已暂停，因为试用额度已用完；进度已保存，可以继续。');
    const fenced = missionOutcomePresentation({
      type: 'life.mission.completed', status: 'parked', park_reason: 'provider_fence', success: false, outcome_class: 'ended',
    } as never, 'zh-CN');
    expect(fenced.label).toContain('模型服务当前不接受调用');
  });
});
