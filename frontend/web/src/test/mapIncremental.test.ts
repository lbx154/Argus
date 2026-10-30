import { expect, it } from 'vitest';
import type { Dataset } from '../map/model';
import { mergeMapProgress } from '../map/incremental';
const first: Dataset = {
  id: 'live:s-map',
  tasks: [{ id: 'a', title: 'Method', status: 'running', deps: [], ts: 1 }],
  events: [{ id: 'e1', item_id: 'a', type: 'handoff', role: 'engineer', to: 'reviewer', ts: 2, text: 'Check' }], cursor: '2',
};
it('replaces the full card list, preserves letters and stable object identities across line cursors', () => {
  const next = mergeMapProgress(first, { ...first, tasks: [...first.tasks, { id: 'b', title: 'Validate', status: 'pending', deps: ['a'], ts: 3 }],
    events: [{ id: 'e2', item_id: 'b', type: 'handoff', ts: 4, text: 'Ready' }], cursor: '4' });
  expect(next.tasks.map(task => task.id)).toEqual(['a', 'b']);
  expect(next.tasks[0]).toBe(first.tasks[0]);
  expect(next.events[0]).toBe(first.events[0]);
  expect(mergeMapProgress(next, { ...next, events: [] })).toBe(next);
  expect(mergeMapProgress(next, { ...next, tasks: next.tasks.slice(1), events: [] }).events.map(event => event.id)).toEqual(['e2']);
});
it('deduplicates history letters and keeps their recorded order when an older cursor is read', () => {
  const older = { ...first.events[0], id: 'e0', ts: 1 };
  const next = mergeMapProgress(first, { ...first, events: [older, ...first.events] });
  expect(next.events.map(event => event.id)).toEqual(['e0', 'e1']);
  expect(mergeMapProgress(next, { ...first, id: 'live:other', events: [] }).events).toEqual([]);
});
