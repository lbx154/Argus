import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { NodeProps } from '@xyflow/react';
import type { MacroData, MacroNode } from '../map/MacroTaskNode';
import type { Dataset, MapTask } from '../map/model';
import type { ArtifactInfo, DeliveryReceipt, EventMsg } from '../../../core/src/types';

vi.mock('@xyflow/react', async original => ({
  ...await original<typeof import('@xyflow/react')>(), Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const artifactBlob = vi.fn(async () => new Blob(['png']));
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, artifactBlob } };
});
vi.mock('../i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../i18n')>();
  const t = (key: string, variables?: Record<string, string | number>) => actual.translate(key, variables, 'zh-CN');
  return { ...actual, useI18n: () => ({ locale: 'zh-CN', setLocale: vi.fn(), t }) };
});

const { taskFigures } = await import('../map/cardFigure');
const { CardNow } = await import('../map/LiveLine');
const { CardFigure, MacroTaskNode, MapArtifactContext } = await import('../map/MacroTaskNode');
const { mergeMapProgress } = await import('../map/incremental');
const { layoutSubmap } = await import('../map/submap');
const { MapCardFigureContext } = await import('../map/cardContext');

const WS = '/tenant/state/workspaces/s-1';
// Trimmed from the 2026-10-28 CartPole trial run, round 2 of task 1f5b.
const events: EventMsg[] = [
  { type: 'round.start', round_index: 2, item_id: 't1', ts: 100 },
  { type: 'engineer.progress', kind: 'tool_use', tool_name: 'view', call_id: 'c1', status: 'running', item_id: 't1', ts: 110,
    agent_layer: 'engineer', text: `view: {"path": "${WS}/figures/learning_curve.png"}` },
  { type: 'engineer.progress', kind: 'tool_use', tool_name: 'view', call_id: 'c2', status: 'running', item_id: 't1', ts: 111,
    agent_layer: 'engineer', text: `view: {"path": "${WS}/figures/time_to_threshold.png"}` },
  { type: 'engineer.progress', kind: 'agent_message', item_id: 't1', ts: 120, agent_layer: 'engineer',
    text: '15次正式运行均已完成，累计900,000次训练交互。' },
  { type: 'engineer.progress', kind: 'command_execution', call_id: 'c3', status: 'running', item_id: 't1', ts: 130,
    agent_layer: 'engineer', tool_name: 'bash', text: '.venv/bin/python analysis.py --run runs/main --out results' },
];
const image = (path: string): ArtifactInfo => ({ path, name: path.split('/').pop()!, kind: 'image',
  mime: 'image/png', size: 1, mtime: 1, source: 'manager_live' });

let tree: ReactTestRenderer | undefined;
afterEach(() => { act(() => tree?.unmount()); tree = undefined; artifactBlob.mockClear(); });

describe('the task holder, the latest thing said and results on the map card', () => {
  const task: MapTask = { id: 't1', title: 'Totals', objective: '', status: 'in_progress', role: 'engineer',
    latest_action: { role: 'planner', text: 'Total the **columns**.', ts: 1 } };
  const card = (value: MapTask): NodeProps<MacroNode> => ({ id: value.id, data: {
    id: value.id, task: value, ordinal: 1, part: 1, partCount: 1, start: 1, end: 0, totalSteps: 0,
    layout: layoutSubmap(value, [], true, []), frame: { width: 1440, height: 900, scale: 1 },
    canvasSize: { width: 1440, height: 960 }, zh: true, focused: false, detailed: false, open: vi.fn(), readStep: vi.fn(),
  } as MacroData } as NodeProps<MacroNode>);

  it('refreshes the holder and the latest thing said, as text rather than Markdown, through the existing map merge', () => {
    const first: Dataset = { id: 'live:p', kind: 'live', title: '', description: '', read_only: false, tasks: [task], events: [] };
    act(() => { tree = create(<MacroTaskNode {...card(first.tasks[0])} />); });
    expect(tree!.root.findByProps({ 'data-testid': 'map-card-owner' }).children.join('')).toBe('负责人：工程师');
    const said = tree!.root.findByProps({ 'data-testid': 'map-card-action' });
    expect(said.props.title).toBe('Total the **columns**.');
    expect(said.findByType('strong').children).toEqual(['columns']);
    const next = mergeMapProgress(first, { ...first, incremental: true, tasks: [{ ...task, role: 'reviewer',
      latest_action: { role: 'reviewer', text: '正在核对总数。', ts: 2 } }] });
    act(() => tree!.update(<MacroTaskNode {...card(next.tasks[0])} />));
    // Who said it is who holds it: the holder is not named twice.
    expect(tree!.root.findAllByProps({ 'data-testid': 'map-card-owner' })).toHaveLength(0);
    expect(tree!.root.findByProps({ 'data-testid': 'map-card-action' }).props.title).toBe('正在核对总数。');
    act(() => tree!.update(<CardNow task={{ id: 't2', title: 'Other', objective: '', status: 'pending' }} zh />));
    expect(tree!.root.findAllByProps({ 'data-testid': 'map-card-now' })).toHaveLength(0);
  });

  it('opens every file from the closing handoff with the existing preview callback, in order', () => {
    const open = vi.fn();
    const first: Dataset = { id: 'live:p', kind: 'live', title: '', description: '', read_only: false, tasks: [task], events: [] };
    const next = mergeMapProgress(first, { ...first, incremental: true, tasks: [{ ...task, status: 'done', result_paths: ['report.md', 'data.csv'] }] });
    const shown = (value: MapTask) => <MapArtifactContext.Provider value={{ onOpenArtifact: open }}><MacroTaskNode {...card(value)} /></MapArtifactContext.Provider>;
    act(() => { tree = create(shown(first.tasks[0])); });
    expect(tree!.root.findAllByProps({ title: 'report.md' })).toHaveLength(0);
    act(() => tree!.update(shown(next.tasks[0])));
    const result = tree!.root.findByProps({ title: 'report.md' });
    expect(result.children.join('')).toBe('report.md');
    act(() => result.props.onClick());
    act(() => tree!.root.findByProps({ title: 'data.csv' }).props.onClick());
    expect(open.mock.calls).toEqual([['report.md'], ['data.csv']]);
    expect(result.parent?.type).toBe('div');
    act(() => tree!.update(shown({ ...task, status: 'done', latest_action: { role: 'reviewer', text: 'See report.md', ts: 3 }, result_paths: [] })));
    expect(tree!.root.findAll(node => node.type === 'button' && node.children.includes('打开结果'))).toHaveLength(0);
  });

  it('shows the pending question and distinguishes waiting for an answer from stopping', () => {
    const waiting = { ...task, pending_question: '请问输出用中文还是英文？' };
    const props = card(waiting);
    act(() => { tree = create(<MacroTaskNode {...props} />); });
    expect(tree!.root.findByProps({ 'data-testid': 'map-card-question' }).children.join('')).toBe(waiting.pending_question);
    expect(tree!.root.findByProps({ className: 'map-status' }).children).toContain('等你回答');
    expect(tree!.root.findAllByProps({ 'data-testid': 'map-card-live' })).toHaveLength(0);
    const workStatus = { state: 'paused', reason: 'operator_input' } as NonNullable<MacroData['workStatus']>;
    const request = card({ ...task, id: 'q0', role: 'manager', turn_kind: 'qa' });
    act(() => tree!.update(<MacroTaskNode {...request} data={{ ...request.data, workStatus }} />));
    expect(tree!.root.findByProps({ className: 'map-status' }).children).toContain('等你回答');
    act(() => tree!.update(<MacroTaskNode {...request} data={{ ...request.data, workStatus: { ...workStatus, reason: 'not_running' } }} />));
    expect(tree!.root.findByProps({ className: 'map-status' }).children).toContain('已停止');
  });

  it('shows a live clock only for the request being answered, from its actual call start', () => {
    vi.spyOn(Date, 'now').mockReturnValue(200_000);
    const request = card({ ...task, id: 'q0', role: 'manager', turn_kind: 'qa', started_ts: 1 });
    act(() => { tree = create(<MacroTaskNode {...request} />); });
    expect(tree!.root.findByProps({ className: 'map-card-kind' }).children).toContain('请求');
    expect(tree!.root.findByProps({ className: 'map-status' }).children).toContain('进行中');
    expect(tree!.root.findAllByProps({ 'data-testid': 'map-card-live' })).toHaveLength(0);
    act(() => tree!.update(<MacroTaskNode {...request} data={{ ...request.data,
      liveCall: { role: 'manager', answers: 'raw-request', request_card_id: 'q0', started_ts: 185 } }} />));
    const clock = tree!.root.findByProps({ 'data-testid': 'map-card-live' });
    expect(tree!.root.findByProps({ className: 'map-status' }).children).toContain('正在回答');
    expect(clock.findByType('span').children.join('')).toBe('正在回复你的消息 · 本次已进行 15 秒');
    expect(tree!.root.findByProps({ 'data-testid': 'map-card-action' }).props.title).toBe(task.latest_action!.text);
    vi.restoreAllMocks();
  });
});

describe('the figure on a task’s card', () => {
  it('prefers the delivered picture, else the project’s main figure the task’s work named', () => {
    const exposed = [image('figures/learning_curve.png'), image('figures/time_to_threshold.png')];
    expect(taskFigures([], exposed, events, WS).get('t1')).toBe('figures/learning_curve.png');
    const delivered = { delivery_id: 'd', item_id: 't1', delivered_at: 1, kind: 'task_completed', title: '', summary: '',
      status: 'done', review_status: 'done', schema_version: 1,
      primary_target: { path: 'REPORT_zh.md' }, targets: [{ path: 'figures/time_to_threshold.png' }] } as unknown as DeliveryReceipt;
    expect(taskFigures([delivered], exposed, events, WS).get('t1')).toBe('figures/time_to_threshold.png');
    // A picture the project does not expose cannot load, so it is not offered.
    expect(taskFigures([], [], events, WS).has('t1')).toBe(false);
  });

  it('keeps the loaded picture when a poll hands down a new context value', async () => {
    const shown = (figures: Map<string, string>) => (
      <MapCardFigureContext.Provider value={{ sid: 's-1', figures }}><CardFigure taskId="t1" title="CartPole" /></MapCardFigureContext.Provider>
    );
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:figure', revokeObjectURL: () => {} });
    await act(async () => { tree = create(shown(new Map([['t1', 'figures/learning_curve.png']]))); });
    const img = tree!.root.findByType('img');
    expect(img.props.src).toBe('blob:figure');
    await act(async () => { tree!.update(shown(new Map([['t1', 'figures/learning_curve.png'], ['t2', 'x.png']]))); });
    expect(tree!.root.findByType('img')).toBe(img);
    expect(artifactBlob).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });
});
