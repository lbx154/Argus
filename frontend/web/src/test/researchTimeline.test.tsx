import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../research-workbench/api';
import { TimelinePage } from '../research-workbench/timeline/TimelinePage';
import { TaskEditor } from '../research-workbench/timeline/TaskEditor';
import { WORKBENCH_MODULES } from '../research-workbench/modules';
import type { TimelineEntry, TimelineInput, TimelineReport } from '../research-workbench/timeline/types';

vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'zh-CN' }) }));
vi.mock('../research-workbench/api', () => ({ api: { timelineLatest: vi.fn(), timelineExample: vi.fn(), timelineEstimate: vi.fn(), timelineSave: vi.fn() } }));

const example: TimelineInput = { selected_proposal_id: 'a', resources: { gpu: 2 }, now_hours: 0, deadline_hours: 120, defer_optional: true,
  proposals: [{ id: 'a', title: '研究方案', tasks: [{ id: 'pilot', title: '验证假设', phase: 'validation', duration_hours: [2, 4, 12], basis: '未校准估计' }] }] };
const report: TimelineReport = { selected_proposal_id: 'a', proposals: [{ id: 'a', title: '研究方案', finish_hours: { lower: 2, expected: 5, upper: 12 }, remaining_hours: 5, deadline_gap_hours: 0,
  schedule: [{ id: 'pilot', title: '验证假设', phase: 'validation', status: 'pending', start_hours: 0, finish_hours: 5, resource_wait_hours: 0, resources: {}, basis: '未校准估计', reason: '' }], blocked_tasks: [], failed_tasks: [], deferred_task_ids: [] }] };
let renderer: ReactTestRenderer;
const button = (text: string) => renderer.root.findAllByType('button').find((node) => node.children.join('') === text)!;
const labelInput = (text: string) => renderer.root.findAllByType('label').find((node) => node.children[0] === text)!.findByType('input');
async function mount() { await act(async () => { renderer = create(<TimelinePage sid="demo" />); }); }
async function settleEstimate() { await act(async () => { await vi.advanceTimersByTimeAsync(351); }); }

beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks();
  vi.mocked(api.timelineLatest).mockResolvedValue({ latest: null });
  vi.mocked(api.timelineExample).mockResolvedValue(structuredClone(example));
  vi.mocked(api.timelineEstimate).mockResolvedValue(report);
});
afterEach(() => { if (renderer) act(() => renderer.unmount()); vi.useRealTimers(); });

it('creates and saves a typed plan from user-entered estimates without loading sample data', async () => {
  const draftReport: TimelineReport = { ...report, selected_proposal_id: 'idea-1', proposals: [{
    ...report.proposals[0], id: 'idea-1', title: '验证我的研究问题',
    schedule: [{ ...report.proposals[0].schedule[0], id: 'task-1', title: '准备公开数据' }],
  }] };
  vi.mocked(api.timelineEstimate).mockResolvedValue(draftReport);
  await mount();
  expect(button('创建计划草稿').props.disabled).toBe(true);
  act(() => {
    labelInput('研究目标 / 计划名称').props.onChange({ target: { value: '验证我的研究问题' } });
    labelInput('第一个任务').props.onChange({ target: { value: '准备公开数据' } });
    ['乐观工期（小时）', '最可能工期（小时）', '悲观工期（小时）'].forEach((label, i) =>
      labelInput(label).props.onChange({ target: { value: String([1, 3, 6][i]) } }));
    renderer.root.findAllByType('label').find(node => node.children[0] === '估计依据')!.findByType('textarea')
      .props.onChange({ target: { value: '仅作排期假设，未实测' } });
  });
  expect(button('创建计划草稿').props.disabled).toBe(false);
  act(() => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }));
  await settleEstimate();
  const input: TimelineInput = {
    selected_proposal_id: 'idea-1', resources: {}, now_hours: 0, deadline_hours: null, defer_optional: false,
    proposals: [{ id: 'idea-1', title: '验证我的研究问题', tasks: [{
      id: 'task-1', title: '准备公开数据', phase: 'planning', basis: '仅作排期假设，未实测', duration_hours: [1, 3, 6],
    }] }],
  };
  expect(api.timelineEstimate).toHaveBeenCalledWith(input, expect.any(AbortSignal));
  expect(api.timelineExample).not.toHaveBeenCalled();
  expect(api.timelineSave).not.toHaveBeenCalled();
  expect(JSON.stringify(renderer.toJSON())).toContain('当前估计不含资源排队时间');
  vi.mocked(api.timelineSave).mockResolvedValue({ version: 1, input, report: draftReport, reason: '创建研究计划', created_at: '' });
  await act(async () => button('保存计划版本').props.onClick());
  expect(api.timelineSave).toHaveBeenCalledWith('demo', input, 0, '创建研究计划');
  vi.mocked(api.timelineEstimate).mockResolvedValue(report);
  await act(async () => button('加载论文示例').props.onClick());
  await settleEstimate();
  expect(labelInput('方案名称').props.value).toBe('研究方案');
  expect(JSON.stringify(renderer.toJSON())).toContain('5.0 h');
});

it('exposes the timeline in workbench navigation and calculates edited deadlines', async () => {
  expect(WORKBENCH_MODULES.find((m) => m.id === 'timeline')?.zh).toBe('研究排期');
  await mount();
  await act(async () => { await button('加载论文示例').props.onClick(); });
  await settleEstimate();
  expect(JSON.stringify(renderer.toJSON())).toContain('5.0 h');
  act(() => labelInput('期望完成时间（小时）').props.onChange({ target: { value: '3' } }));
  await settleEstimate();
  expect(vi.mocked(api.timelineEstimate).mock.lastCall?.[0].deadline_hours).toBe(3);
});

it('saves the edited input with the loaded version and reloads it after remount', async () => {
  await mount();
  await act(async () => { await button('加载论文示例').props.onClick(); });
  await settleEstimate();
  const entry: TimelineEntry = { version: 1, input: example, report, reason: '演示', created_at: '' };
  vi.mocked(api.timelineSave).mockResolvedValue(entry);
  await act(async () => { await button('保存计划版本').props.onClick(); });
  expect(api.timelineSave).toHaveBeenCalledWith('demo', example, 0, '加载演示 proposal');
  expect(JSON.stringify(renderer.toJSON())).toContain('已保存版本 1');
  act(() => renderer.unmount());
  vi.mocked(api.timelineLatest).mockResolvedValue({ latest: entry });
  await mount();
  expect(JSON.stringify(renderer.toJSON())).toContain('已保存 v1');
  expect(labelInput('期望完成时间（小时）').props.value).toBe(120);
});

it('keeps the edited draft and reports a save conflict without retrying or overwriting', async () => {
  await mount();
  await act(async () => { await button('加载论文示例').props.onClick(); });
  await settleEstimate();
  vi.mocked(api.timelineSave).mockRejectedValue(new Error('timeline version conflict'));
  await act(async () => { await button('保存计划版本').props.onClick(); });
  expect(JSON.stringify(renderer.toJSON())).toContain('timeline version conflict');
  expect(labelInput('期望完成时间（小时）').props.value).toBe(120);
  expect(api.timelineSave).toHaveBeenCalledTimes(1);
});

it('invalid estimates disable saving and surface the actual error', async () => {
  vi.mocked(api.timelineEstimate).mockRejectedValue(new Error('dependency cycle'));
  await mount();
  await act(async () => { await button('加载论文示例').props.onClick(); });
  await settleEstimate();
  expect(button('保存计划版本').props.disabled).toBe(true);
  expect(JSON.stringify(renderer.toJSON())).toContain('dependency cycle');
});

it('deadline edits render the new range, changed schedule and execution tradeoffs', async () => {
  vi.mocked(api.timelineExample).mockResolvedValue({ ...example, adapt_to_deadline: true });
  vi.mocked(api.timelineEstimate).mockImplementation(async (input) => input.deadline_hours === 3 ? {
    ...report, proposals: [{ ...report.proposals[0], finish_hours: { lower: 1, expected: 2, upper: 4 },
      schedule: [{ ...report.proposals[0].schedule[0], finish_hours: 2 }],
      adaptation: { enabled: true, target_hours: 3, status: 'fits', baseline_finish_hours: { lower: 2, expected: 5, upper: 12 },
        changes: [{ kind: 'execution_option', id: 'pilot', title: '验证假设', option_title: '复用实现', reason: '已有可复用实现', tradeoff: '减少实现自由度', from_duration_hours: [2, 4, 12], to_duration_hours: [1, 2, 4] }] },
    }],
  } : report);
  await mount();
  await act(async () => { await button('加载论文示例').props.onClick(); });
  await settleEstimate();
  act(() => labelInput('期望完成时间（小时）').props.onChange({ target: { value: '3' } }));
  await settleEstimate();
  const rendered = JSON.stringify(renderer.toJSON());
  expect(renderer.root.findAllByType('p').map((p) => p.children.join('')).join(' ')).toContain('1.0–4.0 h');
  expect(rendered).toContain('复用实现');
  expect(rendered).toContain('减少实现自由度');
  expect(renderer.root.findAllByProps({ role: 'img' })[0].props['aria-label']).toContain('0.0–2.0 h');
  act(() => labelInput('期望完成时间（小时）').props.onChange({ target: { value: '120' } }));
  await settleEstimate();
  expect(renderer.root.findAllByType('p').map((p) => p.children.join('')).join(' ')).toContain('2.0–12.0 h');
});

it('starting work adopts the forecast execution option instead of the original resource estimate', () => {
  const update = vi.fn();
  const forecast = { ...report.proposals[0], schedule: [{ ...report.proposals[0].schedule[0], duration_hours: [1, 2, 4] as [number, number, number], resources: { gpu: 2 }, execution_option_id: 'parallel' }] };
  act(() => { renderer = create(<TaskEditor input={{ ...example, adapt_to_deadline: true }} forecast={forecast} onChange={update} text={(zh) => zh} />); });
  act(() => renderer.root.findByProps({ 'aria-label': '进展状态' }).props.onChange({ target: { value: 'running' } }));
  const started = update.mock.lastCall![0].proposals[0].tasks[0];
  expect(started).toMatchObject({ status: 'running', resources: { gpu: 2 }, duration_hours: [1, 2, 4], remaining_hours: [1, 2, 4], execution_option_id: 'parallel' });
});
