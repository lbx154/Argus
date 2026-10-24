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
    expect(teamPhase(status('step_finished'))).toBe('done');
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
