import { act, create } from 'react-test-renderer';
import { expect, it, vi } from 'vitest';
import { useMapGrowth } from '../map/useMapGrowth';
import { stepIdentity, type GrowthBatch, type GrowthScene } from '../map/growth';
const scene = (ids: string[]): GrowthScene => ({ cards: [{ id: 'task' }], links: [], layouts: { task: { steps: ids.map((id) => ({ id })), links: [] } } });
it('keeps concurrent entrances alive and retires each batch on its own deadline', async () => {
  vi.useFakeTimers();
  let value!: GrowthBatch;
  function Probe({ data }: { data: GrowthScene }) { value = useMapGrowth(data); return null; }
  let renderer!: ReturnType<typeof create>;
  try {
    act(() => { renderer = create(<Probe data={scene(['brief'])} />); });
    act(() => renderer.update(<Probe data={scene(['brief', 'run'])} />));
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    act(() => renderer.update(<Probe data={scene(['brief', 'run', 'review'])} />));
    expect(Object.keys(value.steps)).toEqual([stepIdentity('task', 'run'), stepIdentity('task', 'review')]);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(Object.keys(value.steps)).toEqual([stepIdentity('task', 'review')]);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(value.steps).toEqual({});
  } finally { act(() => renderer?.unmount()); vi.useRealTimers(); }
});
