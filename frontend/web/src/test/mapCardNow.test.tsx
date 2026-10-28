import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ArtifactInfo, DeliveryReceipt, EventMsg } from '../../../core/src/types';

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

const { taskNow } = await import('../lib/liveActivity');
const { taskFigures } = await import('../map/cardFigure');
const { CardNow } = await import('../map/LiveLine');
const { CardFigure } = await import('../map/MacroTaskNode');
const { MapCardFigureContext, MapCardNowContext } = await import('../map/cardContext');

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
const image = (path: string): ArtifactInfo => ({ path, name: path.split('/').pop()!, why: '', exists: true, kind: 'image',
  mime: 'image/png', size: 1, mtime: 1, source: 'manager_live' });

let tree: ReactTestRenderer | undefined;
afterEach(() => { act(() => tree?.unmount()); tree = undefined; artifactBlob.mockClear(); });

describe('what a running task’s map card says it is doing', () => {
  it('reads the newest thing said and the command still running', () => {
    const now = taskNow(events, 't1', 90, 'zh-CN', WS);
    expect(now.said?.text).toBe('15次正式运行均已完成，累计900,000次训练交互。');
    expect(now.said?.role).toBe('engineer');
    expect(now.running?.text).toContain('analysis.py');
    // Something after the command means it is no longer the thing under way.
    const later = taskNow([...events, { type: 'engineer.progress', kind: 'reasoning', item_id: 't1', ts: 140, text: '…' }],
      't1', 90, 'zh-CN', WS);
    expect(later.running).toBeNull();
  });

  it('shows those lines on the card of the task they belong to only', () => {
    const value = { taskId: 't1', ...taskNow(events, 't1', 90, 'zh-CN', WS) };
    act(() => { tree = create(<MapCardNowContext.Provider value={value}><CardNow taskId="t1" zh /></MapCardNowContext.Provider>); });
    const said = tree!.root.findByProps({ 'data-testid': 'map-card-said' });
    expect(said.findByType('b').children.join('')).toBe('工程师：');
    expect(tree!.root.findByProps({ 'data-testid': 'map-card-running' }).props.title).toContain('analysis.py');
    act(() => tree!.update(<MapCardNowContext.Provider value={value}><CardNow taskId="t2" zh /></MapCardNowContext.Provider>));
    expect(tree!.root.findAllByProps({ 'data-testid': 'map-card-now' })).toHaveLength(0);
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
