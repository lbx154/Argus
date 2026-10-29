import { describe, expect, it } from 'vitest';
import type { EventMsg } from '../api';
import { renderFeedRows } from '../lib/feedSteps';
import { plainRowText } from '../lib/plainProgress';

const rows = (events: EventMsg[], locale: 'en' | 'zh-CN' = 'zh-CN') => renderFeedRows(events, { locale, showReasoning: false }).list;

describe('the progress feed in the reader’s words', () => {
  it('names the task a round belongs to, so a new task’s round 1 does not read as starting over', () => {
    const [started] = rows([{ type: 'round.review.started', round_index: 1, item_id: 't4', ts: 1 }]);
    expect(plainRowText(started.ev, started.r, 'zh-CN', id => (id === 't4' ? '依据当前证据验收综述' : '')).text).toBe('审阅者开始检查「依据当前证据验收综述」第 1 轮');
    expect(plainRowText(started.ev, started.r, 'en').text).toBe('The Reviewer checks round 1 of this task');
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
  });
});
