import { expect, it } from 'vitest';
import { buildSubmap } from '../map/submap';
import type { MapEvent } from '../map/model';
const task = { id: 't1', title: 'Read the source', status: 'running' };
it('formats workspace paths in actual task letters while preserving their full words', () => {
  const event: MapEvent = { id: 'e', item_id: task.id, type: 'handoff', ts: 1, role: 'engineer', to: 'reviewer',
    text: 'Checked `/work/a/report.md`. '+ 'Full review evidence. '.repeat(30) };
  const step = buildSubmap(task, [event], false, '/work/a')[1];
  expect(step.detail).toBe(event.text.replace('/work/a/', ''));
  expect(step.summary).toBe('Checked `report.md`.');
});
it('names an Engineer continuation without a self-addressed arrow', () => {
  const steps = buildSubmap(task, [{ id: 'e', item_id: task.id, type: 'handoff', ts: 1, role: 'engineer', to: 'engineer', text: 'Continue.' }], false);
  expect(steps[1].title).toBe('Engineer continues');
});
