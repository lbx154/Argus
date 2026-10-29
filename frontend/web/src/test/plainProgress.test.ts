import { describe, expect, it } from 'vitest';
import type { EventMsg } from '../api';
import { renderFeedRows } from '../lib/feedSteps';
import { plainRowText, roleHeadline } from '../lib/plainProgress';

const rows = (events: EventMsg[], locale: 'en' | 'zh-CN' = 'zh-CN') => renderFeedRows(events, { locale, showReasoning: false }).list;

describe('the progress feed in the reader’s words', () => {
  it('names the task a round belongs to, so a new task’s round 1 does not read as starting over', () => {
    const [started] = rows([{ type: 'round.review.started', round_index: 1, item_id: 't4', ts: 1 }]);
    expect(plainRowText(started.ev, started.r, 'zh-CN', id => (id === 't4' ? '依据当前证据验收综述' : '')).text).toBe('审阅者开始检查「依据当前证据验收综述」第 1 轮');
    expect(plainRowText(started.ev, started.r, 'en').text).toBe('The Reviewer checks round 1 of this task');
  });


  it('never heads a role with a command, and counts commands when it said nothing', () => {
    const command = { type: 'engineer.progress', kind: 'command_execution', agent_layer: 'reviewer', text: "/usr/bin/bash -lc 'cat research-plan.md'", ts: 3 } as EventMsg;
    const said = { type: 'engineer.progress', kind: 'agent_message', agent_layer: 'reviewer', text: '我会通读当前综述。\n\ncontinue', ts: 2 } as EventMsg;
    expect(roleHeadline(rows([said, command]), 'zh-CN')).toBe('我会通读当前综述。');
    expect(roleHeadline(rows([command, command]), 'zh-CN')).toBe('运行了 2 条命令');
    expect(roleHeadline(rows([command], 'en'), 'en')).toBe('Ran 1 command');
  });

  it('heads a role with what it said, not with its task closing', () => {
    const said = { type: 'engineer.progress', kind: 'agent_message', agent_layer: 'engineer', text: 'b.py 写好了，输出 2。', item_id: 't2', ts: 2 } as EventMsg;
    const closed = { type: 'life.mission.completed', item_id: 't2', status: 'done', success: true, ts: 3 } as EventMsg;
    expect(roleHeadline(rows([said, closed]), 'zh-CN')).toBe('b.py 写好了，输出 2。');
  });
});

describe('a host record in a role’s line', () => {
  it('keeps the sentence in the line and folds the technical record away', () => {
    const reason = '审阅者的会话在得出结论前就结束了，这一轮没有评审意见。 技术记录：exit=1; fatal_error=query: HTTP 402: 402 试用额度不足';
    const [row] = rows([{ type: 'round.review.completed', review_skipped: true, status: 'blocked', reason, round_index: 2, ts: 1 }]);
    const said = plainRowText(row.ev, row.r, 'zh-CN');
    expect(said.text).toBe('这一轮没有审阅 · 审阅者的会话在得出结论前就结束了，这一轮没有评审意见。');
    expect(said.technical).toContain('技术记录：exit=1; fatal_error=query: HTTP 402');
    expect(said.full).toContain('技术记录：exit=1');
    expect(roleHeadline([row], 'zh-CN')).not.toContain('技术记录');
  });
});
