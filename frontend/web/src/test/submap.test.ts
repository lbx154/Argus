import { expect, it } from 'vitest';
import { buildMap, type MapEvent, type MapTask } from '../map/model';
import { buildSubmap, frameForSubmap, layoutScene, layoutSubmap, zoomTarget } from '../map/submap';
const task: MapTask = { id: 't1', title: 'Check the source', status: 'done' };
const letter = (id: string, role: string, to: string, text: string, ts: number): MapEvent => ({ id, item_id: task.id, type: 'handoff', role, to, text, ts });
it('reads the task question and operator answer together with the team letters', () => {
  const events = [letter('ask', 'engineer', 'operator', 'Which dataset?', 1), letter('answer', 'operator', 'engineer', 'Use 2024.', 2),
    letter('review', 'engineer', 'reviewer', 'Ready to check.', 3)];
  const steps = buildSubmap(task, events, false);
  expect(steps.map(step => step.detail)).toEqual([task.title, 'Which dataset?', 'Use 2024.', 'Ready to check.', 'This task was completed.']);
  expect(steps[1].title).toBe('Engineer → You');
  expect(steps[2].title).toBe('You → Engineer');
});
it('deduplicates letters, excludes other tasks and orders by recorded time', () => {
  const early = letter('early', 'engineer', 'reviewer', 'Ready.', 1);
  const late = letter('late', 'reviewer', 'manager', 'Accepted.', 2);
  const steps = buildSubmap(task, [late, early, early, { ...early, id: 'other', item_id: 't2' }], false);
  expect(steps.flatMap(step => step.eventIds)).toEqual(['early', 'late']);
  expect(steps[2].kind).toBe('review');
});
it('reads Manager replies and clarifications without inventing work stages', () => {
  const qa: MapTask = { ...task, turn_kind: 'qa' };
  const events: MapEvent[] = [{ id: 'reply', item_id: qa.id, type: 'turn.replied', ts: 1, text: 'Here is the answer.' },
    { id: 'more', item_id: qa.id, type: 'turn.asked', ts: 2, text: 'And the cost?' }];
  expect(buildSubmap(qa, events, false).map(step => step.title)).toEqual(['What you asked', 'What Argus answered', 'Your clarification']);
  expect(buildSubmap(qa, [], false).at(-1)?.detail).toBe('No answer recorded.');
});
it('keeps pages of letters within one task card and stacks their content on phones', () => {
  const events = Array.from({ length: 36 }, (_, i) => letter(`e${i}`, 'engineer', 'reviewer', `Letter ${i}`, i));
  const scene = layoutScene(buildMap([task]), events, false);
  expect(scene.cards).toHaveLength(1);
  expect(scene.allCards).toHaveLength(4);
  const layout = layoutSubmap(task, events, false, undefined, 0, true);
  expect(new Set(Object.values(layout.positions).map(position => position.x)).size).toBe(1);
  const frame = frameForSubmap(layout);
  expect(layout.width * frame.scale).toBeLessThanOrEqual(frame.width);
  expect(layout.height * frame.scale).toBeLessThanOrEqual(frame.height);
});
it('locates the pointer or focal task using the actual card bounds and ignores hidden cards', () => {
  const nodes = [{ id: 'one', position: { x: 100, y: 200 }, width: 900, height: 875 }];
  const view = { x: 50, y: 80, zoom: 0.5 };
  expect(zoomTarget(nodes, view, { x: 200, y: 300 }, { x: 0, y: 0 })).toBe('one');
  expect(zoomTarget(nodes, view, { x: 0, y: 0 }, { x: 200, y: 300 })).toBe('one');
  expect(zoomTarget([{ ...nodes[0], hidden: true }], view, { x: 200, y: 300 }, { x: 0, y: 0 })).toBeNull();
  expect(zoomTarget(nodes, view, { x: 900, y: 900 }, { x: 0, y: 0 })).toBeNull();
});
