import { describe, expect, it } from 'vitest';
import type { EventMsg } from '../api';
import { earlierRoleWork } from '../components/agentActivityModel';
import { emptyMissionView } from '../../../core/src/missionView';
import { renderFeedRows } from '../lib/feedSteps';
import { plainInternalReason, plainRowText, roleHeadline } from '../lib/plainProgress';

const rows = (events: EventMsg[], locale: 'en' | 'zh-CN' = 'zh-CN') => renderFeedRows(events, { locale, showReasoning: false }).list;
const climb = 'Nothing changed since the reviewer last held this work, and nothing new arrived from outside, so it went one rung up, to the planner.';

describe('the progress feed in the reader’s words', () => {
  it('says the host’s English routing reason in Chinese and in English', () => {
    // What it means for the work, never the internal hand-off between roles.
    expect(plainInternalReason(climb, 'zh-CN')).toBe('这条路没有新进展，团队正在换个思路。');
    expect(plainInternalReason(climb, 'en')).toBe('This path is making no new progress, so the team is trying a different approach.');
    expect(plainInternalReason(climb.replace('planner.', 'operator.'), 'zh-CN')).toBe('这条路没有新进展，需要你决定怎么继续。');
    // The Reviewer's own words after the hand-on stay.
    expect(plainInternalReason(`${climb} The Reviewer had handed it to the planner: 计划需要调整。`, 'zh-CN'))
      .toBe('这条路没有新进展，团队正在换个思路。计划需要调整。');
    // A role's own words are left as written.
    expect(plainInternalReason('综述内容已通过完整独立核查。', 'zh-CN')).toBe('综述内容已通过完整独立核查。');
  });

  it('names the task a round belongs to, so a new task’s round 1 does not read as starting over', () => {
    const [started] = rows([{ type: 'round.review.started', round_index: 1, item_id: 't4', ts: 1 }]);
    expect(plainRowText(started.ev, started.r, 'zh-CN', id => (id === 't4' ? '依据当前证据验收综述' : '')).text).toBe('审阅者开始检查「依据当前证据验收综述」第 1 轮');
    expect(plainRowText(started.ev, started.r, 'en').text).toBe('The Reviewer checks round 1 of this task');
  });

  it('reads an earlier verdict as its reason in one line, with the status and next step folded', () => {
    // Trial s-7d7b7d22: "结果未被接受，需要再试一次。 · 综述内容已通过… 下一步：…(a page)".
    const view = emptyMissionView();
    view.role_work = [
      { id: 'v1', ts: 1, role: 'reviewer', kind: 'verdict', status: 'continue', item_id: 't2', title: '结果未被接受，需要再试一次。',
        detail: '综述内容已通过完整独立核查；核验记录对既有回复的一处错误归因尚需修正。\n\n下一步：修正 source-verification.md 第 55 行的归因。' },
      { id: 'v2', ts: 2, role: 'reviewer', kind: 'verdict', status: 'replan_requested', item_id: 't2', title: '结果已核对，计划需要调整后才能继续。', detail: climb },
    ] as typeof view.role_work;
    const [continued, climbed] = rows(earlierRoleWork(view, [{ type: 'ui.operator', ts: 99 } as EventMsg], 'zh-CN'));
    const first = plainRowText(continued.ev, continued.r, 'zh-CN');
    expect(first.text).toBe('综述内容已通过完整独立核查；核验记录对既有回复的一处错误归因尚需修正。');
    expect(first.full).toContain('下一步：修正 source-verification.md 第 55 行的归因。');
    expect(plainRowText(climbed.ev, climbed.r, 'zh-CN').text).toBe('这条路没有新进展，团队正在换个思路。');
    expect(roleHeadline([continued, climbed], 'zh-CN')).toBe('这条路没有新进展，团队正在换个思路。');
  });

  it('never heads a role with a command, and counts commands when it said nothing', () => {
    const command = { type: 'engineer.progress', kind: 'command_execution', agent_layer: 'reviewer', text: "/usr/bin/bash -lc 'cat research-plan.md'", ts: 3 } as EventMsg;
    const said = { type: 'engineer.progress', kind: 'agent_message', agent_layer: 'reviewer', text: '我会通读当前综述。\n\ncontinue', ts: 2 } as EventMsg;
    expect(roleHeadline(rows([said, command]), 'zh-CN')).toBe('我会通读当前综述。');
    expect(roleHeadline(rows([command, command]), 'zh-CN')).toBe('运行了 2 条命令');
    expect(roleHeadline(rows([command], 'en'), 'en')).toBe('Ran 1 command');
  });
});
