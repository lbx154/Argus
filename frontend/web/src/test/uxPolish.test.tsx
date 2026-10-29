import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { Snapshot } from '../../../core/src/types';

vi.mock('../i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../i18n')>();
  return {
    ...actual,
    useI18n: () => ({ locale: 'zh-CN', setLocale: vi.fn(), t: (key: string, variables?: Record<string, string | number>) => actual.translate(key, variables, 'zh-CN') }),
  };
});

const { TeamPauseButton, teamPhase, teamPhaseLabel } = await import('../components/TeamControls');
const { translate } = await import('../i18n');
const { LaunchSettingsBar } = await import('../components/LaunchPanel');
const { ContinuousSwitch } = await import('../components/ContinuousSwitch');
const { DEFAULT_LAUNCH_SETTINGS, DEFAULT_ASK_MODE, launchForMessage } = await import('../lib/launch');

const snap = (alive: boolean, backlog: Array<{ status: string }> = [], extra: Partial<Snapshot['daemon']> = {}) => ({
  daemon: { alive, pid: alive ? 1 : null, uptime_seconds: null, backend: null, ...extra },
  backlog: backlog.map((item, index) => ({ id: `t${index}`, title: '', objective: '', priority: 1, ...item })),
}) as unknown as Snapshot;
const status = (state: string, reason = '') => ({ state, reason, role: '', taskId: '', title: '', activityAt: null, activityAgeSeconds: null }) as never;

describe('team status and controls', () => {
  it('names the four states a person asks about', () => {
    expect(teamPhase(status('running'))).toBe('running');
    expect(teamPhase(status('paused', 'operator_input'))).toBe('waiting_you');
    expect(teamPhase(status('paused', 'not_running'))).toBe('paused');
    expect(teamPhase(status('idle'))).toBe('done');
    expect(['running', 'waiting_you', 'paused', 'done'].map((phase) => teamPhaseLabel(phase as never, 'zh-CN')))
      .toEqual(['运行中', '等你回复', '已暂停', '已完成']);
  });

  it('offers pause for a live team and resume for a stopped team with unfinished work', () => {
    const pause = renderToStaticMarkup(<TeamPauseButton snapshot={snap(true)} locale="zh-CN" onPause={() => {}} onResume={() => {}} />);
    expect(pause).toContain('暂停团队');
    const resume = renderToStaticMarkup(<TeamPauseButton snapshot={snap(false, [{ status: 'paused' }])} locale="zh-CN" onPause={() => {}} onResume={() => {}} />);
    expect(resume).toContain('继续');
    expect(renderToStaticMarkup(<TeamPauseButton snapshot={snap(false, [{ status: 'done' }])} locale="zh-CN" onResume={() => {}} />)).toBe('');
    expect(renderToStaticMarkup(<TeamPauseButton snapshot={snap(true, [], { draining: true })} locale="zh-CN" onPause={() => {}} />)).toContain('disabled');
  });

  it('keeps "stop this reply" apart from pausing the team', () => {
    expect(translate('chat.stopWaiting', undefined, 'zh-CN')).toBe('停止本次回复');
    expect(translate('topbar.pauseTeam', undefined, 'zh-CN')).toBe('暂停团队');
    expect(translate('topbar.resumeTeam', undefined, 'zh-CN')).toBe('继续');
  });
});

describe('launch setting: when to ask me', () => {
  it('defaults to asking only beyond authority and maps each choice to the real hold', () => {
    expect(DEFAULT_ASK_MODE).toBe('beyond_authority');
    expect(launchForMessage(DEFAULT_LAUNCH_SETTINGS, 'beyond_authority', true)?.hold).toBe(false);
    expect(launchForMessage(DEFAULT_LAUNCH_SETTINGS, 'first_task', true)?.hold).toBe(true);
    expect(launchForMessage(DEFAULT_LAUNCH_SETTINGS, 'first_task', false)).toBeUndefined();
    expect(launchForMessage(DEFAULT_LAUNCH_SETTINGS, 'every_task', false)).toMatchObject({ hold: true, target_level: 'auto' });
    expect(launchForMessage(DEFAULT_LAUNCH_SETTINGS, 'beyond_authority', false)).toBeUndefined();
  });

  it('folds to one line after work started and hides research knobs on non-research projects', () => {
    const props = { value: DEFAULT_LAUNCH_SETTINGS, onChange: () => {}, ask: 'every_task' as const, onAskChange: () => {} };
    const started = renderToStaticMarkup(<LaunchSettingsBar {...props} started />);
    expect(started).toContain('<details');
    expect(started).toContain('每个新任务都先给我看计划');
    expect(started).not.toContain('首个结果');
    const software = renderToStaticMarkup(<LaunchSettingsBar {...props} research={false} />);
    expect(software).not.toContain('研究定位');
    expect(software).not.toContain('成果形式');
    const unknown = renderToStaticMarkup(<LaunchSettingsBar {...props} research={null} />);
    expect(unknown).toContain('研究类项目的选项');
  });
});

describe('continuous work switch', () => {
  it('is a labelled switch that says what it does and reflects the project state', () => {
    const off = renderToStaticMarkup(<ContinuousSwitch enabled={false} onToggle={vi.fn()} />);
    expect(off).toContain('role="switch"');
    expect(off).toContain('aria-checked="false"');
    expect(off).toContain('持续推进');
    expect(off).toContain('做完一项自动安排下一项，不用每次等你');
    const on = renderToStaticMarkup(<ContinuousSwitch enabled pending onToggle={vi.fn()} />);
    expect(on).toContain('aria-checked="true"');
    expect(on).toContain('disabled=""');
  });
});

describe('questions, paths and header', async () => {
  const { splitReplyOptions, readableQuestions, ReplyOptionButtons, ReplyChoiceContext } = await import('../components/ReplyOptions');
  const { relativizeWorkspacePaths } = await import('../lib/workspacePaths');

  it('turns numbered options into buttons and keeps the rest of the text', () => {
    const text = '“更适合发票”希望采用哪套规则？\n\n可选（回复编号即可）：\n1. 金额 1,234.50；百分比 12.50%\n2. 金额 1,234.50；百分比 12.5%\n\n直接回复，或在网页端处理。';
    const { body, options } = splitReplyOptions(text);
    expect(options).toEqual(['金额 1,234.50；百分比 12.50%', '金额 1,234.50；百分比 12.5%']);
    expect(body).not.toContain('回复编号');
    expect(body).toContain('直接回复');
    const html = renderToStaticMarkup(<ReplyChoiceContext.Provider value={() => {}}><ReplyOptionButtons options={options} zh /></ReplyChoiceContext.Provider>);
    expect(html.match(/<button/g)).toHaveLength(2);
    expect(html).toContain('也可以在下方直接写你的回答');
    expect(splitReplyOptions('没有选项的普通回复').options).toEqual([]);
  });

  it('lists several questions packed into one line', () => {
    const line = '请确认以下几点：数据从哪里来？需要覆盖哪些年份？结果用什么格式交付？是否需要附上原始代码？';
    expect(readableQuestions(line)).toContain('\n2. 需要覆盖哪些年份？');
  });

  it('shows workspace-relative paths', () => {
    const root = '/fastdata/v-boxiuli/argus-trial-1023';
    expect(relativizeWorkspacePaths(`在 ${root}/demo/c1-review 的 moneyfmt 中，见 [README](${root}/demo/c1-review/README.md)`, root))
      .toBe('在 demo/c1-review 的 moneyfmt 中，见 [README](demo/c1-review/README.md)');
  });
});
