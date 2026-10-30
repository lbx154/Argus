import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import type { Snapshot } from '../../../core/src/types';

vi.mock('../i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../i18n')>();
  return {
    ...actual,
    useI18n: () => ({ locale: 'zh-CN', setLocale: vi.fn(), t: (key: string, variables?: Record<string, string | number>) => actual.translate(key, variables, 'zh-CN') }),
  };
});

const { RunControl, teamPhase } = await import('../components/TeamControls');

const snap = (alive: boolean, backlog: Array<{ status: string; title?: string }> = [], extra: Partial<Snapshot['daemon']> = {}) => ({
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
  });

  it('stops with one control that first says what it interrupts, and keeps what was written', () => {
    const working = { ...snap(true, [{ status: 'in_progress', title: '写综述' }]), roles: [
      { role: 'manager', active: false, tasks: [] }, { role: 'engineer', active: true, tasks: ['t0'] },
    ] } as unknown as Snapshot;
    const onStop = vi.fn();
    let renderer!: ReactTestRenderer;
    act(() => { renderer = create(<RunControl snapshot={working} pending onStop={onStop} onResume={() => {}} />); });
    act(() => renderer.root.findByProps({ 'data-stop-work': true }).props.onClick());
    const explains = renderer.root.findByProps({ 'data-stop-explains': true });
    expect(explains.findAllByType('li').map(item => item.children.join(''))).toEqual(['经理正在写的回复', '工程师在「写综述」上的工作']);
    expect(explains.findAllByType('p').map(line => line.children.join('')).join('')).toContain('已经写好的文件和记录都会保留');
    expect(onStop).not.toHaveBeenCalled();
    act(() => renderer.root.findByProps({ 'data-stop-confirm': true }).props.onClick());
    expect(onStop).toHaveBeenCalledOnce();
  });

  it('keeps one button through stopping and resuming', () => {
    const running = { ...snap(true), roles: [] } as Snapshot;
    const stopped = { ...snap(false, [], { resumable: true }), roles: [] } as Snapshot;
    const onResume = vi.fn();
    let renderer!: ReactTestRenderer;
    const show = (snapshot: Snapshot) => <RunControl snapshot={snapshot} onStop={() => {}} onResume={onResume} />;
    act(() => { renderer = create(show(running)); });
    const button = renderer.root.findByProps({ 'data-stop-work': true });
    act(() => renderer.update(show(stopped)));
    expect(renderer.root.findByProps({ 'data-resume-work': true })).toBe(button);
    act(() => button.props.onClick());
    expect(onResume).toHaveBeenCalledOnce();
    act(() => renderer.update(show(running)));
    expect(renderer.root.findByProps({ 'data-stop-work': true })).toBe(button);
    act(() => renderer.unmount());
  });

  it('keeps the control focusable while busy and ignores another action', () => {
    const onResume = vi.fn();
    const stopped = { ...snap(false, [], { resumable: true }), roles: [] } as Snapshot;
    let renderer!: ReactTestRenderer;
    act(() => { renderer = create(<RunControl snapshot={stopped} busy onStop={() => {}} onResume={onResume} />); });
    const button = renderer.root.findByProps({ 'data-resume-work': true });
    expect(button.props.disabled).toBeUndefined();
    expect(button.props['aria-disabled']).toBe(true);
    act(() => button.props.onClick());
    expect(onResume).not.toHaveBeenCalled();
    act(() => renderer.unmount());
  });

  it('offers Resume only for work the server says stopped short, and nothing on finished work', () => {
    const resume = renderToStaticMarkup(<RunControl snapshot={snap(false, [{ status: 'in_progress' }], { resumable: true })} onStop={() => {}} onResume={() => {}} />);
    expect(resume).toContain('data-resume-work');
    expect(resume).not.toContain('data-stop-work');
    expect(renderToStaticMarkup(<RunControl snapshot={snap(false, [{ status: 'in_progress' }])} onStop={() => {}} onResume={() => {}} />)).toBe('');
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

it('returns a disappearing focused run control to the composer without taking focus from elsewhere', () => {
  const node = {};
  const doc = { activeElement: node };
  vi.stubGlobal('document', doc);
  const focusComposer = vi.fn();
  let renderer: ReactTestRenderer;
  const show = (alive: boolean) => <RunControl snapshot={{ ...snap(alive), roles: [] }} onStop={() => {}} onResume={() => {}} onFocusComposer={focusComposer} />;
  act(() => { renderer = create(show(true), { createNodeMock: element => element.type === 'button' ? node : null }); });
  act(() => renderer!.update(show(false)));
  expect(focusComposer).toHaveBeenCalledOnce();
  act(() => renderer!.update(show(true)));
  doc.activeElement = {};
  act(() => renderer!.update(show(false)));
  expect(focusComposer).toHaveBeenCalledOnce();
  act(() => renderer!.unmount()); vi.unstubAllGlobals();
});
