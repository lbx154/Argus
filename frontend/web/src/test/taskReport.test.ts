import { describe, expect, it } from 'vitest';
import { plainTaskReport } from '../lib/plainStatus';
import { CONTINUED_TASK_REPORT, CONTINUED_TASK_TITLE, INTERRUPTED_TASK_REASON, INTERRUPTED_TASK_REPORT } from './taskReportFixtures';

describe('historical task-report presentation', () => {
  it('localizes the recorded continuation without translating the research title or certifying a review', () => {
    const report = plainTaskReport(CONTINUED_TASK_REPORT, 'zh-CN')!;
    expect(report.text).toContain(`当时记录：工作继续推进。\n任务：${CONTINUED_TASK_TITLE}`);
    expect(report.text).toContain('当时记录的审阅状态：已完成');
    expect(report.text).toContain('当时尚未排入下一项任务');
    expect(report.text).not.toMatch(/Task continued|review=|Nothing is queued|独立|复核通过|审阅通过|研究已完成/);
    expect(report.technical).toBe('');
  });

  it('says the recorded reason as written, with its receipt aside, and keeps the later next-action line visible', () => {
    const report = plainTaskReport(INTERRUPTED_TASK_REPORT, 'zh-CN')!;
    expect(report.text).toContain('当时这项任务未能完成。');
    expect(report.text).toContain(`当时记录的原因：${INTERRUPTED_TASK_REASON}`);
    expect(report.text).toContain('当时记录的下一步：诊断当时的原因，再选择可恢复的方案。');
    expect(report.text).not.toMatch(/Technical record|External interrupt|Next:/);
    expect(report.technical).toBe('Technical record: error=External interrupt: daemon stop requested');
    const english = plainTaskReport(INTERRUPTED_TASK_REPORT, 'en')!;
    expect(english.text).toContain('This task had not completed at that time.');
    expect(english.text).toContain(`Reason recorded at the time: ${INTERRUPTED_TASK_REASON}`);
    expect(english.text).toContain('Next step recorded at the time:');
    expect(english.text).not.toMatch(/will diagnose|Technical record/);
    expect(plainTaskReport(CONTINUED_TASK_REPORT, 'en')!.text).toContain('At that time, nothing was queued');
  });

  it('handles the Chinese wrapper', () => {
    const raw = `未能完成：当前任务。\n原因：${INTERRUPTED_TASK_REASON} 技术记录：error=External interrupt: daemon stop requested\n下一步：Argus 会诊断原因并选择可恢复的方案。`;
    const report = plainTaskReport(raw, 'zh-CN')!;
    expect(report.text).toContain('当时这项任务未能完成。');
    expect(report.text).toContain(`当时记录的原因：${INTERRUPTED_TASK_REASON}`);
    expect(report.text).toContain('当时记录的下一步：诊断当时的原因');
    expect(report.technical).toBe('技术记录：error=External interrupt: daemon stop requested');
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

  it('says a recorded stop in the reader’s language from its stop kind, keeping the original aside', () => {
    // Hosted trial: an old receipt read in English inside the Chinese UI.
    const reason = 'The work was paused before the Engineer finished this round because the model provider is not accepting calls right now, so this round was not judged; it resumes from the saved progress.';
    const raw = `Could not complete Survey the field.\nReason: ${reason} Technical record: stop_kind=provider_fence; error=HTTP 403`;
    const report = plainTaskReport(raw, 'zh-CN')!;
    expect(report.text).toContain('当时记录的原因：在工程师完成这一轮之前，工作因为模型服务当前不接受调用而暂停，这一轮没有评判；会从保存的进度继续。');
    expect(report.text).not.toMatch(/The work was paused|not accepting calls/);
    expect(report.technical).toBe('Technical record: stop_kind=provider_fence; error=HTTP 403');
    // The clause comes from the code, not the sentence it was written in.
    const cooled = plainTaskReport(raw.replace('provider_fence', 'provider_cooldown'), 'zh-CN')!;
    expect(cooled.text).toContain('工作因为模型服务要求 Argus 稍后再调用而暂停');
    expect(plainTaskReport(raw, 'en')!.text).toContain(`Reason recorded at the time: ${reason}`);
    // The oldest wording named its code inline.
    const legacy = plainTaskReport('Could not complete the current task.\nReason: Backend call paused before a trustworthy completed turn (stop_kind=daemon_shutdown); reviewer skipped. error=SIGTERM', 'zh-CN')!;
    expect(legacy.text).toContain('当时记录的原因：在工程师完成这一轮之前，工作因为Argus 后台服务停止或重启了而暂停，这一轮没有评判；会从保存的进度继续。');
    expect(legacy.text).not.toMatch(/Backend call paused|reviewer skipped/);
  });

  it('localizes a stored pause receipt’s reason and advice by the stop it records, and leaves other receipts alone', () => {
    // Hosted trial: "原因：The work was paused before the Engineer finished this round because …" in a Chinese UI.
    const raw = '模型服务限制了调用，「调研」先停下了。\n原因：The work was paused before the Engineer finished this round because the model provider is not accepting calls right now, so this round was not judged; it resumes from the saved progress.\n建议：Resume from the saved progress once the budget or model-service condition that paused the work has cleared.\n问题解决后点「继续运行」就能接着做；在那之前不会自动重试。';
    const report = plainTaskReport(raw, 'zh-CN')!;
    expect(report.text).toBe('模型服务限制了调用，「调研」先停下了。\n原因：在工程师完成这一轮之前，工作因为模型服务当前不接受调用而暂停，这一轮没有评判；会从保存的进度继续。\n建议：等预算或模型服务的限制解除后，从保存的进度继续。\n问题解决后点「继续运行」就能接着做；在那之前不会自动重试。');
    expect(plainTaskReport(raw.replace('the model provider is not accepting calls right now', 'the Argus background service stopped or restarted'), 'zh-CN')!.text)
      .toContain('工作因为Argus 后台服务停止或重启了而暂停');
    // English readers already read it as written; a clause no stop kind says stays as recorded.
    expect(plainTaskReport(raw.replace(/^.*\n/, 'Stuck for now: Survey.\n'), 'en')).toBeNull();
    expect(plainTaskReport(raw.replace('the model provider is not accepting calls right now', 'something new happened'), 'zh-CN')!.text)
      .toContain('原因：The work was paused before the Engineer finished this round because something new happened');
    expect(plainTaskReport('「调研」暂时做不下去了。\n原因：数据集下载失败。', 'zh-CN')).toBeNull();
  });
});
