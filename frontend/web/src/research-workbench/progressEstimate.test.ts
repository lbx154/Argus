import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { emptyMissionView } from '../../../core/src/missionView';
import { deriveProgressEstimate } from './progressEstimate';
import { ExperimentsPage } from './pages/ExperimentsPage';
import type { ActiveWorkbenchPageProps } from './pages/pageTypes';
import type { EventMsg, Snapshot } from './types';
import { translate } from '../i18n';

vi.mock('./useWorkbenchText', () => ({
  useWorkbenchText: () => ({ locale: 'en', text: (_zh: string, en: string) => en, t: (key: string) => translate(key, {}, 'en') }),
}));

function snapshot(overrides: { alive?: boolean; activeStatus?: string; reviewStatus?: string; complete?: boolean } = {}): Snapshot {
  const complete = overrides.complete ?? false;
  const backlog = [
    { id: 'done', title: 'Completed task', objective: '', status: 'done', priority: 1, started_ts: 100, finished_ts: 300 },
    { id: 'active', title: 'Current task', objective: 'Run probe', status: complete ? 'done' : overrides.activeStatus ?? 'running', priority: 2, started_ts: 900, finished_ts: complete ? 990 : null },
  ];
  const view = emptyMissionView();
  Object.assign(view.mission, { id: 'active', title: 'Current task', objective: 'Run probe', status: complete ? 'complete' : 'working', started_at: 900, completed_at: complete ? 990 : null, elapsed_seconds: 100, campaign_started_at: 100, campaign_elapsed_seconds: 900 });
  view.stage = { id: 'experiment', label: 'Experiment' };
  view.round = { current: 1, max: 3 };
  view.active_role = complete ? '' : 'engineer';
  view.dag = backlog.map((item) => ({ ...item, deps: [], branch_id: item.id, parent_branch_id: null }));
  view.review = { status: overrides.reviewStatus ?? '', reason: '', rejected_attempts: 0 };
  view.updated_at = 1_000;
  return {
    session: { id: 's-test', display_name: 'Test', objective: '', last_active: 1_000, cwd: '/tmp', workdir: '/tmp' },
    daemon: { alive: overrides.alive ?? true, pid: 1, uptime_seconds: 500, backend: 'pi', global_daily_cap_usd: null, health: { state: overrides.alive === false ? 'stopped' : 'active', seconds_since_progress: 2 } },
    roles: [
      { role: 'planner', backend: 'pi', backend_label: 'Pi', model: 'm', effort: null, active: false, label: 'done', status: 'done', age_s: 1 },
      { role: 'engineer', backend: 'pi', backend_label: 'Pi', model: 'm', effort: null, active: !complete, label: 'running command', status: complete ? 'done' : 'running', age_s: 1 },
    ],
    backlog,
    recent_events: [],
    mission_view: view,
  };
}

const events: EventMsg[] = [
  { type: 'engineer.progress', ts: 850, item_id: 'active', kind: 'command_execution', text: 'old command', action_summary: 'old step' },
  { type: 'engineer.progress', ts: 950, item_id: 'active', kind: 'command_execution', text: 'python run_probe.py', action_summary: 'running probe' },
];
const reviewEvent = (overrides: EventMsg = {}): EventMsg => ({ type: 'round.review.completed', ts: 980, item_id: 'active', round_index: 1, status: 'done', reason: 'Evidence checked.', ...overrides });
const reviewCheckpoint = (result: ReturnType<typeof deriveProgressEstimate>) => result.checkpoints.find((checkpoint) => checkpoint.id === 'review');

describe('deriveProgressEstimate', () => {
  it('counts completed work without converting command activity into research progress', () => {
    const result = deriveProgressEstimate(snapshot(), events, 1_000);
    const busy = deriveProgressEstimate(snapshot(), [...events, ...Array.from({ length: 30 }, (_, i) => ({ ...events[1], ts: 951 + i }))], 1_000);
    expect(result.currentStep).toBe('running probe');
    expect(result.currentDetail).toContain('run_probe.py');
    expect(result.workCompletion).toBe(.5);
    expect(busy.workCompletion).toBe(result.workCompletion);
    expect(result.workScope).toContain('不代表整体目标已成立');
    expect(result.etaUnavailableReason).toContain('没有可比较的固定工作量基线');
  });

  it('keeps bookkeeping receipts out of the current action and public work timeline', () => {
    const receipts = ['usage.recorded', 'agent.io.complete', 'budget.reservation.settled', 'provider.request.completed'].map((type, i) => ({ type, ts: 970 + i, item_id: 'active', text: 'Internal receipt' }));
    const result = deriveProgressEstimate(snapshot(), [...events, ...receipts], 1_000);
    expect(result.currentStep).toBe('running probe');
    expect(result.taskEvents).toHaveLength(1);
    expect(result.currentDetail).not.toContain('Internal receipt');
  });

  it('strips control footers from public prose while preserving identical text in tool output', () => {
    const prose = 'SUMMARY=Checked the lemma.\nDecision: continue\nMILESTONE_STATUS=pending\nNEXT_ACTION=Check the boundary case.';
    const result = deriveProgressEstimate(snapshot(), [...events, { type: 'engineer.progress', item_id: 'active', ts: 990, kind: 'assistant_message', text: prose }], 1_000);
    expect(result.currentDetail).toBe('Checked the lemma.\nCheck the boundary case.');
    expect(result.taskEvents.at(-1)?.text).toBe(result.currentDetail);
    const toolResult = deriveProgressEstimate(snapshot(), [...events, { type: 'engineer.progress', item_id: 'active', ts: 990, kind: 'command_execution', text: prose }], 1_000);
    expect(toolResult.currentDetail).toBe(prose);
  });

  it('does not replace a useful action with an empty handoff or a structured reviewer payload', () => {
    const result = deriveProgressEstimate(snapshot(), [...events,
      { type: 'engineer.progress', item_id: 'active', ts: 990, kind: 'assistant_message', text: 'Decision: continue\nNEXT_OWNER=reviewer', action_summary: 'Decision: continue' },
      { type: 'engineer.progress', item_id: 'active', ts: 991, kind: 'assistant_message', agent_layer: 'reviewer', text: '{"status":"done","reason":"checked"}' },
    ], 1_000);
    expect(result.currentStep).toBe('running probe');
    expect(result.taskEvents).toHaveLength(1);
  });

  it('records the end of a failed round without claiming a usable result was submitted', () => {
    const result = deriveProgressEstimate(snapshot(), [...events, { type: 'round.main.completed', item_id: 'active', round_index: 1, ts: 980, status: 'failed', exit_code: 1 }], 1_000, 'en');
    const ended = result.checkpoints.find((checkpoint) => checkpoint.id === 'handoff');
    expect(ended?.label).toBe('Round execution ended');
    expect(ended?.detail).toContain('results still need to be checked');
    expect(result.review.state).toBe('pending');
    expect(result.workCompletion).toBe(.5);
  });

  it('excludes earlier attempts and another concurrent task from the current task timeline', () => {
    const value = snapshot();
    value.backlog.push({ ...value.backlog[1], id: 'other', title: 'Other task' });
    const result = deriveProgressEstimate(value, [...events,
      { ...events[1], ts: 970, item_id: 'other', action_summary: 'Foreign task action' },
      { ...events[1], ts: 980, item_id: undefined, action_summary: 'Unattributed action' },
    ], 1_000);
    expect(result.currentTaskId).toBe('active');
    expect(result.taskEvents).toHaveLength(1);
    expect(result.taskEvents[0].action_summary).toBe('running probe');
    expect(result.currentDetail).toContain('run_probe.py');
    expect(result.runtime.state).toBe('unknown');
    expect(result.currentStep).toBe('当前运行状态暂不可读');
  });

  it('does not claim current execution or grow elapsed time after the daemon stops', () => {
    const result = deriveProgressEstimate(snapshot({ alive: false }), events, 1_000);
    expect(result.runtime.state).toBe('paused');
    expect(result.currentStep).toBe('当前任务未在运行');
    expect(result.etaUnavailableReason).toContain('运行已停止');
    expect(result.elapsedSeconds).toBe(50);
    expect(deriveProgressEstimate(snapshot({ alive: false }), events, 2_000).elapsedSeconds).toBe(50);
    expect(result.checkpoints.every((checkpoint) => checkpoint.status !== 'active')).toBe(true);
  });

  it('reports a completed work item without inventing a passed review or a completed project', () => {
    const result = deriveProgressEstimate(snapshot({ complete: true }), events, 1_000);
    expect(result.workCompletion).toBe(1);
    expect(result.currentStep).toBe('这一步已结束');
    expect(result.elapsedSeconds).toBe(90);
    expect(reviewCheckpoint(result)?.status).toBe('pending');
    expect(result.review.state).toBe('pending');
  });

  it.each(['working', 'research_incomplete', 'paused_no_breakthrough', 'exhausted_current_methods'])('keeps all-done open research scoped to its recorded work for status %s', (status) => {
    const value = snapshot({ complete: true });
    value.mission_view!.mission.status = status;
    value.mission_view!.routing.open_ended = true;
    const result = deriveProgressEstimate(value, events, 1_000);
    expect(result.completedTasks).toBe(result.totalTasks);
    expect(result.workCompletion).toBe(1);
    expect(result.currentStep).not.toContain('项目已完成');
    expect(result.review.state).toBe('pending');
    expect(result.etaUnavailableReason).toContain('开放研究');
  });

  it.each([
    [{ status: 'continue' }, 'needs_work'],
    [{ status: 'blocked' }, 'needs_work'],
    [{ status: 'failed' }, 'needs_work'],
    [{ status: undefined }, 'pending'],
    [{ review_skipped: true }, 'skipped'],
    [{ review_source: 'engineer_self_review' }, 'self_checked'],
    [{ review_source: 'unrecognized_source' }, 'pending'],
  ] satisfies Array<[EventMsg, string]>)('does not certify a non-passing or non-independent review: %j', (override, expectedState) => {
    const result = deriveProgressEstimate(snapshot(), [...events, reviewEvent(override)], 1_000);
    expect(result.review.state).toBe(expectedState);
    expect(reviewCheckpoint(result)?.status).not.toBe('done');
  });

  it.each([
    { round_index: 1 },
    { item_id: 'other', round_index: 2 },
    { item_id: undefined, round_index: 2 },
    { type: 'review.completed', round_index: 2 },
  ])('does not reuse a previous round, another task, or an unbound passing verdict: %j', (override) => {
    const value = snapshot({ reviewStatus: 'done' });
    value.mission_view!.round.current = 2;
    value.mission_view!.achievement = { id: 'old-achievement', title: 'Old success', goal: 'Old goal', reviewer_certified: true, certified_at: 800 };
    const result = deriveProgressEstimate(value, [...events, reviewEvent(override)], 1_000);
    expect(result.review.state).toBe('pending');
    expect(reviewCheckpoint(result)?.status).not.toBe('done');
  });

  it('uses the latest verdict for this task and round, with its scope and next action', () => {
    const result = deriveProgressEstimate(snapshot(), [...events, reviewEvent(), reviewEvent({ ts: 990, status: 'continue', reason: 'The argument assumes an unproved lemma.', next_action: 'Prove the missing lemma.' })], 1_000);
    expect(result.review.state).toBe('needs_work');
    expect(result.review.detail).toBe('The argument assumes an unproved lemma.');
    expect(result.nextAction).toBe('Prove the missing lemma.');
    expect(result.review.scope).toContain('第 1 轮');
  });

  it('accepts an explicit same-task same-round verdict only for that round', () => {
    const result = deriveProgressEstimate(snapshot(), [...events, reviewEvent()], 1_000);
    expect(result.review.state).toBe('passed');
    expect(reviewCheckpoint(result)?.status).toBe('done');
    expect(result.review.scope).toContain('整体目标是否成立');
    expect(result.workCompletion).toBe(.5);
    expect(result.checkpoints.find((checkpoint) => checkpoint.id === 'handoff')?.status).not.toBe('done');
  });

  it('can bind a verdict without a round index to an explicit current-round start', () => {
    const value = snapshot();
    value.mission_view!.round.current = 2;
    const result = deriveProgressEstimate(value, [...events,
      { type: 'round.start', item_id: 'active', ts: 960, round_index: 2 },
      reviewEvent({ round_index: undefined }),
    ], 1_000);
    expect(result.review.state).toBe('passed');
  });

  it('does not keep a passing verdict while a newer review is underway', () => {
    const value = snapshot();
    value.mission_view!.active_role = 'reviewer';
    value.roles[1].active = false;
    value.roles.push({ ...value.roles[1], role: 'reviewer', active: true });
    const result = deriveProgressEstimate(value, [...events, reviewEvent(), { type: 'round.review.started', item_id: 'active', round_index: 1, ts: 990 }], 1_000);
    expect(result.review.state).toBe('running');
    expect(reviewCheckpoint(result)?.status).toBe('active');
  });

  it('invalidates an earlier passing verdict when the same round is restarted', () => {
    const result = deriveProgressEstimate(snapshot(), [...events, reviewEvent(),
      { type: 'round.start', item_id: 'active', round_index: 1, ts: 990 },
    ], 1_000);
    expect(result.review.state).toBe('pending');
    expect(reviewCheckpoint(result)?.status).not.toBe('done');
  });

  it('uses a newer round start even when the snapshot still reports the previous round', () => {
    const result = deriveProgressEstimate(snapshot(), [...events, reviewEvent(),
      { type: 'round.start', item_id: 'active', round_index: 2, ts: 990 },
    ], 1_000);
    expect(result.review.state).toBe('pending');
    expect(result.review.scope).toContain('第 2 轮');
  });

  it('does not label a bounded continuous project as open research', () => {
    const value = snapshot();
    value.mission_view!.routing = { ...value.mission_view!.routing, lifetime: 'bounded', continuous: true, open_ended: false };
    value.continuous = { enabled: true, open_ended: false, objective: 'Run the fixed test cases' };
    expect(deriveProgressEstimate(value, events, 1_000).openEnded).toBe(false);
  });

  it('does not restore an earlier passed verdict when a new review is paused', () => {
    const value = snapshot({ alive: false, reviewStatus: 'done' });
    const result = deriveProgressEstimate(value, [...events, reviewEvent(),
      { type: 'round.review.started', item_id: 'active', round_index: 1, ts: 990 },
    ], 1_000);
    expect(result.review.state).toBe('pending');
    expect(reviewCheckpoint(result)?.status).toBe('pending');
  });

  it('does not borrow a different backlog item when the current task has not arrived in the backlog', () => {
    const value = snapshot();
    value.mission_view!.mission.id = 'new-task';
    value.mission_view!.mission.title = 'New task';
    const result = deriveProgressEstimate(value, events, 1_000);
    expect(result.currentTaskId).toBe('new-task');
    expect(result.currentTask).toBe('New task');
    expect(result.taskEvents).toEqual([]);
  });

  it('does not turn failed, paused, or unrelated successful durations into a finish-time baseline', () => {
    const value = snapshot();
    value.backlog.push(...['failed', 'paused_operator', 'paused_no_breakthrough', 'done'].map((status, i) => ({ id: `history-${i}`, title: 'Different workload', objective: '', status, priority: 1, started_ts: 1, finished_ts: 50_000 })));
    const result = deriveProgressEstimate(value, events, 1_000);
    expect(result.etaUnavailableReason).toContain('没有可比较的固定工作量基线');
    expect(result).not.toHaveProperty('eta');
    expect(result).not.toHaveProperty('estimate');
  });

  it('makes replanning visible without inferring a completion percentage', () => {
    const result = deriveProgressEstimate(snapshot({ reviewStatus: 'replan_requested' }), events, 1_000);
    expect(result.review.state).toBe('needs_work');
    expect(result.etaUnavailableReason).toContain('调整工作范围');
    expect(result.workCompletion).toBe(.5);
  });

  it('does not invent elapsed time for a task without a recorded start', () => {
    const value = snapshot();
    value.backlog[1].started_ts = null;
    value.mission_view!.mission.started_at = null;
    const result = deriveProgressEstimate(value, [], 1_000);
    expect(result.elapsedSeconds).toBeNull();
  });

  it('localizes generated explanations without changing project content', () => {
    const result = deriveProgressEstimate(snapshot({ complete: true }), events, 1_000, 'en');
    expect(result.currentStep).toBe('This step has ended');
    expect(result.review.label).toBe('Conclusion awaiting review');
    expect(result.review.scope).toContain('Current work item · Round 1');
    expect(result.currentTask).toBe('Current task');
  });
});

function renderPage(value: Snapshot, taskEvents: EventMsg[] = events) {
  const props: ActiveWorkbenchPageProps = {
    sid: value.session.id,
    project: { id: value.session.id, label: 'Test project', objective: '', cwd: '/tmp', workdir: '/tmp', last_active: 1_000, daemon_alive: value.daemon.alive, daemon_pid: 1, uptime_seconds: 500 },
    snapshot: value, events: taskEvents, connected: false, snapshotUpdatedAt: 1_000, active: true,
    refresh: () => {}, navigate: () => {},
    controls: { start: async () => {}, stop: async () => {}, busy: false, error: '' },
  };
  return renderToStaticMarkup(createElement(ExperimentsPage, props));
}

describe('ExperimentsPage recorded work presentation', () => {
  afterEach(() => vi.useRealTimers());

  it('does not present a non-running role’s previous blocked or completed status as its current state', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    const value = snapshot();
    value.roles.push({ role: 'reviewer', backend: 'pi', backend_label: 'Pi', model: 'm', effort: null,
      active: false, label: 'idle', status: 'blocked', age_s: 600 });
    const team = renderPage(value).match(/<section class="ros-card experiment-team">[\s\S]*?<\/section>/)?.[0] ?? '';
    expect(team).toContain('Working');
    expect(team.match(/Not working/g)).toHaveLength(3);
    expect(team).not.toContain('Blocked');
    expect(team).not.toContain('Completed');
  });

  it('keeps the current command body in a closed raw disclosure', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    const markup = renderPage(snapshot(), [{ ...events[1], text: 'python - <<\'PY\'\nraw_command_payload_marker\nPY' }]);
    const callout = markup.slice(markup.indexOf('class="current-step-callout"'), markup.indexOf('class="progress-number"'));
    expect(callout).toContain('Running a command');
    expect(callout).toContain('Original call record');
    expect(callout).toContain('raw_command_payload_marker');
    expect(callout).not.toMatch(/<details[^>]*\sopen/);
    expect(callout.replace(/<details[\s\S]*?<\/details>/g, '')).not.toContain('raw_command_payload_marker');
  });

  it('shows actual mathematical work and pending checks without a paper stage rail or proof percentage', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    const value = snapshot({ complete: true });
    value.mission_view!.routing.open_ended = true;
    value.mission_view!.stage = { id: 'solve', label: 'Solve the current question' };
    value.backlog[1].acceptance_check = 'Prove the lemma without assuming the full conjecture.';
    const markup = renderPage(value);
    expect(markup).toContain('Completed work');
    expect(markup).toContain('Completed task');
    expect(markup).toContain('Conclusion awaiting review');
    expect(markup).toContain('Prove the lemma without assuming the full conjecture.');
    expect(markup).toContain('Recorded stage');
    expect(markup).toContain('Solve the current question');
    expect(markup).toContain('Open research');
    expect(markup).not.toContain('research-stage-rail');
    expect(markup).not.toContain('Estimated progress');
    expect(markup).not.toContain('Estimate confidence');
    expect(markup).not.toContain('Project complete');
    expect(markup).toContain('aria-valuetext="2 of 2 recorded work items completed"');
  });

  it('does not animate a stopped team member or show another task’s recent action', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    const markup = renderPage(snapshot({ alive: false }), [...events, { ...events[1], item_id: 'other', ts: 990, text: 'Unrelated task output' }]);
    expect(markup).toContain('The current task is not running');
    expect(markup).toContain('How the team works together');
    expect(markup).toContain('Checks results and evidence and identifies gaps');
    expect(markup).not.toContain('is-pulsing');
    expect(markup).not.toContain('checkpoint--active');
    expect(markup).not.toContain('Unrelated task output');
  });

  it('names an incomplete research outcome in the work list', () => {
    const value = snapshot({ alive: false, activeStatus: 'research_incomplete' });
    value.mission_view!.mission.status = 'research_incomplete';
    const markup = renderPage(value);
    expect(markup).toContain('Research incomplete');
    expect(markup).toContain('Conclusion awaiting review');
    expect(markup).not.toContain('Status updated');
  });
});
