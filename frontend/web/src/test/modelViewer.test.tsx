import { useEffect } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EventMsg } from '../../../core/src/types';
import { JointPanel } from '../components/model/JointPanel';
import { SessionIdContext } from '../components/EventStream';
import { MapConversation } from '../map/MapConversation';
import { jointControl, type JointControl } from '../lib/modelFiles';

// three.js never loads here: the card's 3D view is replaced by a probe that
// counts how often it mounts.
const probe = vi.hoisted(() => ({ mounts: 0, props: [] as Array<{ path: string; onOpen?: () => void }> }));
vi.mock('../components/ModelPreview', () => ({
  ModelThumbnail: (props: { path: string; onOpen?: () => void }) => {
    probe.props.push(props);
    useEffect(() => { probe.mounts += 1; }, []);
    return <div data-model-probe={props.path} />;
  },
  ModelPreview: () => null,
}));

const shoulder = jointControl({ name: 'left_shoulder', type: 'revolute', parent: 'a', child: 'b', lower: 5 * Math.PI / 180, upper: 175 * Math.PI / 180 }) as JointControl;
const slide = jointControl({ name: 'left_slide', type: 'prismatic', parent: 'a', child: 'c', lower: 0, upper: 0.06 }) as JointControl;
const mirror = jointControl({ name: 'right_slide', type: 'prismatic', parent: 'a', child: 'd', lower: 0, upper: 0.06, mimic: { joint: 'left_slide', multiplier: 1, offset: 0 } }) as JointControl;

describe('joint panel', () => {
  const render = (extra: Partial<Parameters<typeof JointPanel>[0]> = {}) => {
    const onChange = vi.fn();
    let tree!: ReactTestRenderer;
    act(() => {
      tree = create(<JointPanel controls={[slide, shoulder, mirror]} values={{ left_shoulder: Math.PI / 2 }} onChange={onChange}
        onReset={() => {}} sweeping={false} onToggleSweep={() => {}} subtitle="closure" open onToggleOpen={() => {}} zh={false} {...extra} />);
    });
    return { tree, onChange };
  };

  it('bounds each slider by its limits in display units and reports SI values', () => {
    const { tree, onChange } = render();
    const input = tree.root.findByProps({ 'data-joint': 'left_shoulder' }).findByType('input');
    expect(input.props).toMatchObject({ min: 5, max: 175, value: 90, disabled: false });
    expect(tree.root.findByProps({ 'data-joint': 'left_slide' }).findByType('input').props).toMatchObject({ min: 0, max: 60 });
    act(() => input.props.onChange({ target: { value: '45' } }));
    expect(onChange).toHaveBeenCalledWith('left_shoulder', expect.closeTo(Math.PI / 4, 9));
  });

  it('shows a mimic joint as following, not as a free slider', () => {
    const { tree } = render();
    const row = tree.root.findByProps({ 'data-joint': 'right_slide' });
    expect(row.findByType('input').props.disabled).toBe(true);
    expect(row.findAll((node) => node.children.includes('follows left_slide'))).not.toHaveLength(0);
  });

  it('marks the subtitle as a caution only when a loop closes outside the URDF', () => {
    expect(render({ caution: true }).tree.root.findByProps({ className: 'model-joints-subtitle' }).props['data-caution']).toBe(true);
    expect(render().tree.root.findByProps({ className: 'model-joints-subtitle' }).props['data-caution']).toBeUndefined();
  });
});

describe('a stage result card with a robot model', () => {
  beforeEach(() => {
    vi.stubGlobal('window', { requestAnimationFrame: () => 1, cancelAnimationFrame: () => {}, setInterval: () => 1, clearInterval: () => {} });
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  });
  afterEach(() => vi.unstubAllGlobals());
  const delivery = {
    schema_version: 1, delivery_id: 'd-robot', kind: 'stage_package', item_id: 't', title: 'Five-bar URDF', summary: '', status: 'done',
    review_status: 'done', delivered_at: 2,
    primary_target: { path: 'robot/adjustable_fivebar.urdf', label: 'URDF' },
    targets: [{ path: 'robot/previews/overview.png', label: 'preview' }, { path: 'robot/README.md', label: 'notes' }],
  };
  const events = () => [
    { type: 'ui.operator', text: 'make the URDF', ts: 1, message_id: 'm1' },
    { type: 'ui.argus', text: 'Done.', ts: 2, message_id: 'm2', delivery: { ...delivery } },
  ] as unknown as EventMsg[];
  const view = (onOpenDelivery: (receipt: unknown, path?: string) => void) => <SessionIdContext.Provider value="s-robot">
    <MapConversation events={events()} connected pending={false} zh={false} artifacts={[]}
      onClose={() => {}} onOpenArtifact={() => {}} onOpenDelivery={onOpenDelivery as never} />
  </SessionIdContext.Provider>;

  it('shows the model itself, keeps it mounted across polls, and opens the full viewer on it', () => {
    probe.mounts = 0;
    const first = vi.fn();
    const latest = vi.fn();
    let tree!: ReactTestRenderer;
    act(() => { tree = create(view(first)); });
    const card = tree.root.findByProps({ 'data-delivery-model': 'robot/adjustable_fivebar.urdf' });
    // The model wins over the preview picture; the picture stays in the list.
    expect(tree.root.findAllByProps({ 'data-delivery-thumbnail': 'robot/previews/overview.png' })).toHaveLength(0);
    const node = card.findByProps({ 'data-model-probe': 'robot/adjustable_fivebar.urdf' });
    // A poll hands down fresh event objects and handlers with the same content.
    act(() => tree.update(view(latest)));
    act(() => tree.update(view(latest)));
    expect(probe.mounts).toBe(1);
    expect(tree.root.findByProps({ 'data-model-probe': 'robot/adjustable_fivebar.urdf' })).toBe(node);
    act(() => probe.props.at(-1)!.onOpen!());
    expect(latest).toHaveBeenCalledWith(expect.objectContaining({ delivery_id: 'd-robot' }), 'robot/adjustable_fivebar.urdf');
    expect(first).not.toHaveBeenCalled();
  });
});
