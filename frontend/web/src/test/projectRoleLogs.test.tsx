import { readFileSync } from 'node:fs';
import { QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { projectRecord, type Recorded } from '../../../../packages/core/src/serve/views';
import { ProjectRoleLogs } from '../components/EventStream';
import * as feed from '../lib/feedSteps';
import { roleClient, roleSnapshot } from './serverSnapshot';

const record = readFileSync(new URL('./fixtures/home/projects/p-c855623c/events.jsonl', import.meta.url), 'utf8')
  .trim().split('\n').map(line => JSON.parse(line) as Recorded);
const events = projectRecord(record).roleEvents;
let view: ReactTestRenderer | undefined;
afterEach(() => { act(() => view?.unmount()); view = undefined; vi.restoreAllMocks(); });

it('skips saved log formatting while hidden and can show work or its saved details on the same mount', () => {
  const render = vi.spyOn(feed, 'renderFeedRows');
  const snapshot = roleSnapshot(events);
  const show = (connected: boolean, details = false, taskId?: string) =>
    <QueryClientProvider client={roleClient}><ProjectRoleLogs snapshot={snapshot} connected={connected} details={details} taskId={taskId} /></QueryClientProvider>;
  act(() => { view = create(show(false)); });
  expect(view!.toJSON()).toBeNull();
  expect(render).not.toHaveBeenCalled();
  act(() => view!.update(show(true, false, 'inactive-task')));
  expect(view!.toJSON()).toBeNull();
  expect(render).not.toHaveBeenCalled();
  act(() => view!.update(show(true)));
  expect(view!.root.findAllByProps({ 'data-project-work': true })).toHaveLength(1);
  expect(render).toHaveBeenCalledOnce();
  snapshot.work_status = { ...snapshot.work_status!, state: 'idle', roles: [] };
  render.mockClear();
  act(() => view!.update(show(true)));
  expect(view!.toJSON()).toBeNull();
  expect(render).not.toHaveBeenCalled();
  act(() => view!.update(show(false, true)));
  expect(render).toHaveBeenCalledOnce();
  expect(render.mock.results[0].value.list.length).toBeGreaterThan(0);
  expect(view!.root.findAllByProps({ 'data-project-work': true })).toHaveLength(1);
});

it('says, on a folded role, the heading of what it last thought and when; nothing on one that has not', () => {
  const last = events.at(-1)!;
  const thought = { type: 'engineer.progress', agent_layer: 'engineer', kind: 'reasoning', text: '**Checking the parser**\n\nBoth formats parse.',
    event_id: Number(last.event_id) + 1, ts: Number(last.ts) + 1 };
  const snapshot = roleSnapshot([...events, thought]);
  act(() => { view = create(<QueryClientProvider client={roleClient}><ProjectRoleLogs snapshot={snapshot} connected details /></QueryClientProvider>); });
  const shown = view!.root.findAll(node => node.type === 'section' && typeof node.props['data-role'] === 'string')
    .map(group => [group.props['data-role'], group.findAll(node => node.props['data-role-thought'] === true).map(node => node.children.join(''))]);
  expect(shown).toContainEqual(['engineer', ['Checking the parser']]);
  expect(shown.filter(([role]) => role !== 'engineer').every(([, said]) => said.length === 0)).toBe(true);
});
