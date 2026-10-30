import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { Store } from '../../../../packages/core/src/store';
import { progressMap, type Recorded } from '../../../../packages/core/src/serve/views';
import { buildMap, type Dataset } from '../map/model';
import { layoutScene } from '../map/submap';

it('reads the original request, Manager clarification and operator answer on the same map card', () => {
  const home = mkdtempSync(join(tmpdir(), 'argus-map-replay-'));
  try {
    const store = new Store(home);
    const record = readFileSync(new URL('../../../../packages/core/test/fixtures/map-clarification/events.jsonl', import.meta.url), 'utf8');
    const events = record.trim().split('\n').map(line => JSON.parse(line) as Recorded);
    const original = events[0]!;
    const added = events.find(event => event.type === 'task.added');
    if (original.type !== 'message' || added?.type !== 'task.added') throw new Error('incomplete replay fixture');
    const project = store.create({ request: original.text, workdir: '', backend: 'codex', model: null });
    project.tasks = [{ ...added.task, status: 'closed' }];
    writeFileSync(join(store.dir(project.id), 'events.jsonl'), record);
    const data = progressMap(store, project, 0, []) as unknown as Dataset;
    const scene = layoutScene(buildMap(data.tasks), data.events, true);
    expect(scene.cards.map(card => card.id)).toEqual(['q0', 't1']);
    const steps = scene.layouts.q0.steps;
    expect(steps.map(step => step.detail)).toEqual(expect.arrayContaining([
      original.text, '这三个骰子分别用几面？', '都用 6 面。', added.task.objective,
    ]));
    expect(steps.find(step => step.detail === '都用 6 面。')?.title).toBe('你补充的说明');
    expect(scene.links).toContainEqual(expect.objectContaining({ source: 'q0', target: 't1' }));
  } finally { rmSync(home, { recursive: true, force: true }); }
});
