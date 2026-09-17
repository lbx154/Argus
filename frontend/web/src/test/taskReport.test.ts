import { describe, expect, it } from 'vitest';
import { plainTaskReport } from '../lib/plainStatus';
import { CONTINUED_TASK_REPORT, CONTINUED_TASK_TITLE, INTERRUPTED_TASK_REASON, INTERRUPTED_TASK_REPORT } from './taskReportFixtures';

describe('historical task-report presentation', () => {
  it('localizes the recorded continuation without translating the research title or certifying a review', () => {
    const report = plainTaskReport(CONTINUED_TASK_REPORT, 'zh-CN')!;
    expect(report.text).toContain(`当时记录：工作继续推进。\n任务：${CONTINUED_TASK_TITLE}`);
    expect(report.text).toContain('当时记录的审阅状态：已完成');
    expect(report.text).toContain('当时尚未排入下一项任务');
    expect(report.text).not.toMatch(/Task continued|review=|Nothing is queued|独立|复核通过|研究已完成/);
    expect(report.technical).toBe('');
  });

  it('describes the recorded interruption neutrally and keeps the later next-action line visible', () => {
    const report = plainTaskReport(INTERRUPTED_TASK_REPORT, 'zh-CN')!;
    expect(report.text).toContain('当时这项任务未能完成。');
    expect(report.text).toContain('当时运行被操作员停下，这一轮没有做完；这不是对工作本身的评价');
    expect(report.text).toContain('已做的工作保留，当时没有重试。');
    expect(report.text).toContain('当时记录的下一步：诊断当时的原因，再选择可恢复的方案。');
    expect(report.text).not.toMatch(/Technical record|daemon|External interrupt|Next:|你|研究失败/);
    expect(report.technical).toBe('Technical record: error=External interrupt: daemon stop requested');
    expect(report.technical).not.toContain('Next:');
  });

  it('retains historical attribution in English as well', () => {
    const report = plainTaskReport(INTERRUPTED_TASK_REPORT, 'en')!;
    expect(report.text).toContain('This task had not completed at that time.');
    expect(report.text).toContain('At that time, Argus was stopped by its operator');
    expect(report.text).toContain('no retry was made then');
    expect(report.text).toContain('Next step recorded at the time:');
    expect(report.text).not.toMatch(/you stopped|will diagnose|Technical record/);
    expect(plainTaskReport(CONTINUED_TASK_REPORT, 'en')!.text).toContain('At that time, nothing was queued');
  });

  it('handles the Chinese wrapper without assigning the stop to the reader', () => {
    const raw = `未能完成：当前任务。\n原因：${INTERRUPTED_TASK_REASON} 技术记录：error=External interrupt: daemon stop requested\n下一步：Argus 会诊断原因并选择可恢复的方案。`;
    const report = plainTaskReport(raw, 'zh-CN')!;
    expect(report.text).toContain('当时这项任务未能完成。');
    expect(report.text).toContain('当时运行被操作员停下');
    expect(report.text).not.toContain('你');
    expect(report.text).toContain('当时记录的下一步：诊断当时的原因');
    expect(report.technical).not.toContain('下一步');
  });

  it('keeps an unfamiliar next action and mathematical prose intact across technical-record lines', () => {
    const action = 'For p = 5, compare the two valuations before claiming equality.';
    const prose = 'The phrase External interrupt is a label in this experiment, not a result.';
    const raw = `Could not complete the current task.\r\nTechnical record: exit=2\r\nNext: ${action}\r\n${prose}`;
    const report = plainTaskReport(raw, 'zh-CN')!;
    expect(report.text).toContain(`当时记录的下一步：${action}\r\n${prose}`);
    expect(report.technical).toBe('Technical record: exit=2');
  });

  it.each(['Progress:', 'Mission summary:', '本次进展:', '本次完成：'])('preserves all multiline research following %s, including unfenced template-like lines', prefix => {
    const summary = `${prefix} Let T have dimension 6.\nNext: Compare all six independent directions.\nReason: mission failed.\nTechnical record: is the literal column name in this example.\nNothing is queued yet; the Planner is deciding what comes next.`;
    const report = plainTaskReport(`Task continued · Hodge\n${summary}`, 'zh-CN')!;
    expect(report.text.slice(report.text.indexOf(prefix))).toBe(summary);
    expect(report.technical).toBe('');
  });

  it('preserves unknown reports and code examples rather than inferring status from keywords', () => {
    const unknown = 'The dimension is 6, not six records.\nNext: Check the premise.\nTechnical record: is a column heading.';
    expect(plainTaskReport(unknown, 'zh-CN')).toBeNull();
    expect(plainTaskReport('External interrupt: daemon stop requested', 'zh-CN')).toBeNull();
    const example = '```text\nNext: this is a code example\nTechnical record: keep this line\n```';
    const prose = 'The dimension is 6, not six records.';
    const report = plainTaskReport(`Task continued · Parser work\n${example}\n${prose}`, 'zh-CN')!;
    expect(report.text).toContain(example);
    expect(report.text).toContain(prose);
    expect(report.technical).toBe('');
  });
});
