import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import type { NodeProps } from '@xyflow/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MacroTaskNode, type MacroData, type MacroNode } from '../map/MacroTaskNode';
import { MapTeamProgress } from '../map/MapPanel';
import { layoutScene, layoutSubmap, type SubmapStep } from '../map/submap';
import { buildMap, type MapEvent, type MapTask } from '../map/model';

vi.mock('@xyflow/react', async (original) => ({
  ...await original<typeof import('@xyflow/react')>(),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const task: MapTask = { id: 'parent', title: 'Research portfolio', objective: '', status: 'running', deps: [] };
const worker = (id: string, status: string, kind: SubmapStep['kind'] = 'execution'): SubmapStep => ({
  id, kind, title: id, detail: `Work for ${id}`, status, source: 'team', eventIds: [id],
  teamId: 'research-team', teamTaskId: id, teamRole: kind === 'review' ? 'idea-review' : 'idea-route',
  revision: 'r1',
});
const propsFor = (steps: SubmapStep[], patch: Partial<MacroData> = {}): NodeProps<MacroNode> => ({
  id: 'parent-card',
  data: {
    id: 'parent-card', task, ordinal: 1, part: 1, partCount: 1, start: 1, end: steps.length, totalSteps: steps.length,
    layout: layoutSubmap(task, [], false, steps), frame: { width: 900, height: 650, scale: 1 },
    canvasSize: { width: 1440, height: 960 }, zh: false, focused: true, detailed: true, live: true,
    source: 'live:project', readOnly: false, open: vi.fn(), quote: vi.fn(), menu: vi.fn(), readStep: vi.fn(),
    ...patch,
  },
} as NodeProps<MacroNode>);

let renderer: ReactTestRenderer | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; });

describe('Team work in the map', () => {
  it('shows partial completion instead of a green done claim or stale generated acceptance copy', () => {
    const finished = { ...task, status: 'done' };
    const events: MapEvent[] = [{ id: 'end', item_id: task.id, type: 'life.mission.completed', ts: 10,
      text: 'Read one source route.', success: true, overall_complete: false, campaign_continues: true }];
    const scene = layoutScene(buildMap([finished]), events, false);
    const copy = { cards: Object.fromEntries([task.id, 'end'].map((id) => [id, {
      title: 'Paper accepted.', summary: 'Paper accepted.', detail: 'Paper accepted.', generated_at: 1,
    }])) };
    const props = propsFor(scene.layouts[task.id].steps, { ...scene.cards[0], layout: scene.layouts[task.id], copy });
    const markup = renderToStaticMarkup(<MacroTaskNode {...props} />);
    expect(markup).toContain('Execution ended · goal incomplete');
    expect(markup).toContain('the overall goal is not complete and further work remains');
    expect(markup).toContain('This execution ended');
    expect(markup).not.toContain('map-state-done');
    expect(markup).not.toContain('Paper accepted.');
    act(() => { renderer = create(<MacroTaskNode {...props} />); });
    act(() => renderer!.root.findByProps({ 'data-step-id': 'end' }).props.onClick());
    expect(JSON.stringify(renderer!.toJSON())).toContain('Read one source route.');
    expect(JSON.stringify(renderer!.toJSON())).not.toContain('Paper accepted.');
  });

  it('keeps completed, waiting and failed workers truthful while their parent is running', () => {
    const props = propsFor([worker('complete', 'done'), worker('waiting', 'pending'), worker('failed', 'failed')]);
    act(() => { renderer = create(<MacroTaskNode {...props} />); });
    for (const [id, status] of [['complete', 'done'], ['waiting', 'pending'], ['failed', 'failed']]) {
      const node = renderer!.root.findByProps({ 'data-step-id': id });
      expect(node.props['data-status']).toBe(status);
      expect(node.props['data-active']).toBe(false);
    }
    const markup = renderToStaticMarkup(<MacroTaskNode {...props} />);
    expect(markup).toContain('Subtasks 1/3 done · 0 running');
    expect(markup).toContain('>Failed</small>');
    expect(markup).not.toContain('data-active="true" title="Execute"');
  });

  it('highlights every running worker and its stage without requiring the parent last page', () => {
    const props = propsFor([
      worker('route', 'running'), worker('review', 'running', 'review'), worker('complete', 'done'),
    ], { part: 1, partCount: 2, paused: true });
    act(() => { renderer = create(<MacroTaskNode {...props} />); });
    expect(renderer!.root.findByProps({ 'data-step-id': 'route' }).props['data-active']).toBe(true);
    expect(renderer!.root.findByProps({ 'data-step-id': 'review' }).props['data-active']).toBe(true);
    expect(renderer!.root.findByProps({ 'data-step-id': 'complete' }).props['data-active']).toBe(false);
    const markup = renderToStaticMarkup(<MacroTaskNode {...props} />);
    expect(markup).toContain('History · not current execution');
    expect(markup).toContain('data-step-id="route"');
    expect(markup).toContain('data-step-id="review"');

    const menu = props.data.menu;
    renderer!.root.findByProps({ 'data-step-id': 'review' }).props.onContextMenu({ preventDefault() {}, stopPropagation() {}, clientX: 100, clientY: 100 });
    expect(menu).toHaveBeenCalledWith(expect.objectContaining({
      task_id: 'parent', team_id: 'research-team', team_task_id: 'review', event_ids: ['review'],
    }), { x: 100, y: 100 });
  });

  it('labels the separate subtask totals and deduplicates refreshed worker observations', () => {
    const event = (id: string, status: string): MapEvent => ({ id, status, item_id: 'parent', type: 'team.task', ts: 1, text: '' });
    const markup = renderToStaticMarkup(<MapTeamProgress zh={false} events={[
      event('one', 'running'), event('two', 'running'), event('three', 'failed'), event('one', 'done'),
      { id: 'main', item_id: 'parent', type: 'round.start', ts: 1, text: '' },
    ]} />);
    expect(markup).toContain('aria-label="Subtask progress"');
    expect(markup).toContain('<b>1/3</b> completed');
    expect(markup).toContain('<b>1</b> running');
    expect(markup).toContain('<b>1</b> failed');
    expect(renderToStaticMarkup(<MapTeamProgress zh={false} events={[]} />)).toBe('');
  });

  it('replaces stale Team prose with the new failure evidence even while its reader is open', () => {
    const copy = { cards: { route: {
      title: 'Route', summary: 'Old running summary', detail: 'Old running explanation', generated_at: 1,
      event_ids: ['route'], event_revisions: ['r1'],
    } } };
    act(() => { renderer = create(<MacroTaskNode {...propsFor([worker('route', 'running')], { copy, partCount: 2 })} />); });
    act(() => renderer!.root.findByProps({ 'data-step-id': 'route' }).props.onClick());
    expect(JSON.stringify(renderer!.toJSON())).toContain('Old running explanation');

    const updated = { ...worker('route', 'failed'), revision: 'r2', detail: 'The source check failed: missing authorization.', updatedAt: 10 };
    act(() => renderer!.update(<MacroTaskNode {...propsFor([updated], { copy, partCount: 2 })} />));
    const rendered = JSON.stringify(renderer!.toJSON());
    expect(rendered).toContain(updated.detail);
    expect(rendered).not.toContain('Old running summary');
    expect(rendered).not.toContain('Old running explanation');
    expect(renderer!.root.findByProps({ 'data-step-id': 'route' }).props['data-status']).toBe('failed');
  });
});
