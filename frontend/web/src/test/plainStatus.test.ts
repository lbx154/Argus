import { describe, expect, it } from 'vitest';
import {
  isBookkeepingEvent,
  plainDetail,
  plainEventName,
  plainProgress,
  plainRouteStatus,
  plainStatus,
  plainStopKind,
  plainStopSentence,
} from '../lib/plainStatus';

describe('plainStatus', () => {
  it('turns the labels a stopped mission leaves behind into plain sentences', () => {
    expect(plainStatus('Review not performed', 'zh-CN')).toBe('这一轮没有人审阅。');
    expect(plainStatus('Review not performed', 'en')).toBe('No one reviewed this round.');
    expect(plainStatus('Goal framed', 'zh-CN')).toBe('已经理解这项请求，项目目标已经确定。');
    expect(plainStatus('Planning complete', 'zh-CN')).toBe('下一步的计划已经排好。');
    expect(plainStatus('Mission ended', 'zh-CN')).toBe('任务已结束。');
    expect(plainStatus('Mission ended · paused_daemon_shutdown', 'zh-CN')).toBe('任务已结束（Argus 被暂停）。');
    expect(plainStatus('Mission ended · paused_daemon_shutdown', 'en')).toBe('The task ended — Argus was paused.');
    expect(plainStatus('idle', 'zh-CN')).toBe('空闲');
    expect(plainStatus('Round 9 started', 'zh-CN')).toBe('第 9 轮工作开始。');
    expect(plainStatus('Running round 3', 'en')).toBe('Round 3 is in progress.');
    expect(plainStatus('Stage → idea', 'zh-CN')).toBe('研究进入「选题」阶段。');
    expect(plainStatus('Handed off', 'en')).toBe('Handed off to the next role.');
    expect(plainStatus('Planner failed', 'zh-CN')).toBe('Planner 执行失败');
    expect(plainStatus('0 / 2 complete', 'zh-CN')).toBe('已完成 0 / 2');
    expect(plainStatus('request · completed', 'zh-CN')).toBe('模型调用完成');
  });

  it('trusts a backend sentence in the reader\'s language and translates by kind otherwise', () => {
    const backend = { kind: 'round_not_judged', language: 'zh' };
    expect(plainStatus('这一轮没有人审阅。', 'zh-CN', backend)).toBe('这一轮没有人审阅。');
    expect(plainStatus('这一轮没有人审阅。', 'en', backend)).toBe('No one reviewed this round.');
    expect(plainStatus('', 'en', { kind: 'mission_paused' })).toBe('The task was paused before it finished; its progress is saved and it can be resumed.');
    expect(plainStatus('some text nobody mapped', 'en', { kind: 'unknown_kind', language: 'zh' })).toBe('some text nobody mapped');
    expect(plainStatus('第 9 轮工作开始。', 'en', { kind: 'round_started', language: 'zh' })).toBe('Round 9 started.');
    expect(plainDetail('审阅者的会话在得出结论前就结束了，Argus 会再试一次。', 'en', { cause: 'reviewer_unreachable', language: 'zh', technical: 'exit=1' }))
      .toEqual({ text: "The reviewer's session ended before it reached a conclusion; Argus will try again.", technical: 'exit=1' });
    expect(plainDetail('审阅者的会话在得出结论前就结束了，Argus 会再试一次。', 'zh-CN', { cause: 'reviewer_unreachable', language: 'zh', technical: 'exit=1' }))
      .toEqual({ text: '审阅者的会话在得出结论前就结束了，Argus 会再试一次。', technical: 'exit=1' });
    expect(plainStatus('Existing team storage has source snapshots', 'zh-CN')).toBe('Existing team storage has source snapshots');
    expect(plainStatus('', 'zh-CN')).toBe('');
  });

  it.each([
    ['using a tool', '正在使用工具'],
    ['Running a command', '正在运行命令'],
    ['running project command', '正在运行命令'],
    ['inspecting project state', '正在查看项目状态'],
    ['Reporting progress', '正在汇报进展'],
    ['Working', '正在工作'],
  ])('localizes the generic activity %s even when the view declares Chinese', (raw, expected) => {
    expect(plainStatus(raw, 'zh-CN', { kind: 'live_activity', language: 'zh' })).toBe(expected);
  });

  it('preserves concrete activity descriptions and recognized words inside longer text', () => {
    const source = { kind: 'live_activity', language: 'zh' };
    const detail = '正在核对 Lemma 4，并记录反例。';
    expect(plainStatus(detail, 'zh-CN', source)).toBe(detail);
    const specific = 'Using a tool to verify Lemma 4';
    expect(plainStatus(specific, 'zh-CN', source)).toBe(specific);
  });

  it('keeps a specific status kind authoritative over a generic activity label', () => {
    expect(plainStatus('Working', 'zh-CN', { kind: 'mission_failed', language: 'en' }))
      .toBe('任务没能完成。');
    expect(plainStatus('using a tool', 'zh-CN', { kind: 'progress_command', language: 'en' }))
      .toBe('正在运行命令');
  });

  it('retains the legacy activity fallback for an unrecognized kind', () => {
    expect(plainStatus('using a tool', 'zh-CN', { kind: 'unknown_activity', language: 'en' }))
      .toBe('正在使用工具');
  });

  it('renders statuses, stop kinds, progress and bookkeeping events', () => {
    expect(plainRouteStatus('failed', 'zh-CN')).toBe('失败');
    expect(plainRouteStatus('pending', 'en')).toBe('Waiting');
    expect(plainRouteStatus('mystery', 'en')).toBe('mystery');
    expect(plainStopKind('daemon_shutdown', 'en')).toBe('Argus was paused');
    expect(plainStopSentence('backend_unavailable', 'zh-CN')).toBe('因为模型服务不可用，工作停了下来。');
    expect(plainStopSentence('budget_exhausted', 'en')).toBe('The work stopped because the budget ran out.');
    expect(plainProgress(0, 0, 'zh-CN')).toBe('尚未规划');
    expect(plainProgress(1, 3, 'en')).toBe('1 of 3 done');
    expect(plainEventName('provider.request.started', 'zh-CN')).toBe('开始调用模型');
    expect(plainEventName('daemon.command.completed', 'zh-CN')).toBe('指令已执行');
    expect(plainStatus('using a tool', 'zh-CN')).toBe('正在使用工具');
    expect(plainEventName('life.mission.started', 'zh-CN')).toBeNull();
    expect(isBookkeepingEvent('budget.reservation.settled')).toBe(true);
    expect(isBookkeepingEvent('round.review.completed')).toBe(false);
  });

  it('keeps the reason and moves counters and receipts into the technical remainder', () => {
    const raw = 'Engineer backend failed before a trustworthy completed turn; reviewer skipped. backend_failure_streak=9/2; error=Copilot CLI exited with code 1.';
    expect(plainDetail(raw, 'zh-CN')).toEqual({
      text: '这一轮模型服务没有完成调用，所以没有人审阅。',
      technical: 'backend_failure_streak=9/2; error=Copilot CLI exited with code 1.',
    });
    const withNext = `${raw}\n\nNext action: Retry in a fresh Codex session; do not resume the failed thread. If this repeats, pause the daemon and reduce concurrent Codex load.`;
    expect(plainDetail(withNext, 'zh-CN').text).toBe('这一轮模型服务没有完成调用，所以没有人审阅。\n\n下一步：接下来会在新的会话里重试这一轮。');
    const reviewer = "Reviewer backend unavailable for 2 consecutive attempt(s); failing loud rather than settling the round without a real review. The Reviewer's session ended before it reached a conclusion, so this round was not judged. Runner receipt: exit=1, fatal_error=Copilot CLI exited with code 1.";
    expect(plainDetail(reviewer, 'en')).toEqual({
      text: "The reviewer's model service did not respond 2 times in a row, so this round was not reviewed. The reviewer stopped before reaching a conclusion, so this round was not judged.",
      technical: 'Runner receipt: exit=1, fatal_error=Copilot CLI exited with code 1.',
    });
    expect(plainDetail('The wait after a repeated backend failure ended early: daemon stop requested.', 'zh-CN').text)
      .toBe('在等待重试时 Argus 被要求停止，任务随之暂停。');
    expect(plainDetail('review: skipped (backend failure) — Engineer backend failed before a trustworthy completed turn; reviewer skipped. backend_failure_streak=3/2; error=Copilot CLI exited with code 1.', 'en'))
      .toEqual({ text: 'The model service did not complete this round, so no one reviewed it.', technical: 'backend_failure_streak=3/2; error=Copilot CLI exited with code 1.' });
    expect(plainDetail('status=paused_daemon_shutdown rounds=9 reason=The wait after a repeated backend failure ended early: daemon stop requested.', 'zh-CN').text)
      .toBe('任务已结束（Argus 被暂停），共 9 轮。在等待重试时 Argus 被要求停止，任务随之暂停。');
    expect(plainDetail('planner error: Copilot CLI exited with code 1.; retry later', 'zh-CN'))
      .toEqual({ text: '规划这一步没有完成，稍后会重试。', technical: 'Copilot CLI exited with code 1.; retry later' });
    expect(plainDetail('backend failure; retrying in a fresh Codex session after 15.0s', 'en').text)
      .toBe('The model service call failed; retrying in a fresh session after 15.0 seconds.');
    expect(plainDetail('未能完成：Build the portfolio。\n原因：The wait after a repeated backend failure ended early: daemon stop requested.\n下一步：Argus 会诊断原因并选择可恢复的方案。', 'zh-CN').text)
      .toBe('未能完成：Build the portfolio。\n原因：在等待重试时 Argus 被要求停止，任务随之暂停。\n下一步：Argus 会诊断原因并选择可恢复的方案。');
    expect(plainDetail('The backend has failed the same way 9 times in a row (Copilot CLI exited with code 1.). Waiting 3600s before the next attempt so a standing failure stops costing money; if this is a configuration problem, fix it first.', 'zh-CN'))
      .toEqual({ text: '模型服务已连续 9 次以同样的方式失败；为了不让持续的故障白白花钱，3600 秒后再试。', technical: 'Copilot CLI exited with code 1.' });
  });

  it('passes an unfamiliar detail through untouched', () => {
    const note = 'Python 访问路径可用，外部来源并非真正不可达。';
    expect(plainDetail(note, 'zh-CN')).toEqual({ text: note, technical: '' });
  });
});
