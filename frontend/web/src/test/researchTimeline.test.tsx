import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../research-workbench/api';
import { TimelinePage } from '../research-workbench/timeline/TimelinePage';
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
