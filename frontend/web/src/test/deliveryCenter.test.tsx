import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import type { DeliveryReceipt } from '../../../core/src/types';
import { useDeliveryCenter } from '../useDeliveryCenter';
import { cleanDeliverySummary, deliveryFiles, defaultDeliverySelection } from '../components/deliveryPresentation';
const receipt = (id: string): DeliveryReceipt => ({ schema_version: 1, delivery_id: id, item_id: id, kind: 'task_completed', status: 'done', review_status: 'done', title: id, summary: 'Ready. RESULT=Checks passed.', delivered_at: 1, primary_target: { path: 'index.html', label: 'Website', source: 'delivery', }, targets: [{ path: 'report.md', label: 'Report', source: 'delivery', }] });
afterEach(() => vi.unstubAllGlobals());
it('opens files only when chosen and closes the viewer when switching projects', () => {
  let center!: ReturnType<typeof useDeliveryCenter>;
  function Probe({ sid }: { sid: string }) { center = useDeliveryCenter(sid); return null; }
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(<Probe sid="a" />); });
  expect(center.selection).toBeNull();
  act(() => center.open(receipt('old')));
  expect(center.selection?.path).toBe('index.html');
  act(() => center.selectPath('report.md'));
  expect(center.selection?.path).toBe('report.md');
  act(() => center.close());
  expect(center.selection).toBeNull();
  act(() => center.open(receipt('old'), 'report.md'));
  expect(center.selection?.path).toBe('report.md');
  act(() => center.open(receipt('old'), 'missing.pdf'));
  expect(center.selection?.path).toBe('index.html');
  act(() => renderer.update(<Probe sid="b" />));
  expect(center.selection).toBeNull();
  act(() => renderer.update(<Probe sid="a" />));
  expect(center.selection).toBeNull();
  act(() => renderer.unmount());
});

it('opens the newest real delivery and its primary file', () => {
  const report = { path: 'paper/REVIEW.md', label: 'Review', source: 'delivery', };
  const latest = { ...receipt('review'), primary_target: report, targets: [] };
  const paper = { ...receipt('paper'), primary_target: report, targets: [{ ...report, path: 'paper/main.pdf', label: 'Paper' }] };
  expect(defaultDeliverySelection([latest, paper])).toEqual({ receipt: latest, path: 'paper/REVIEW.md' });
  expect(defaultDeliverySelection([latest, paper])).toEqual({ receipt: latest, path: 'paper/REVIEW.md' });
  expect(defaultDeliverySelection([latest])).toEqual({ receipt: latest, path: 'paper/REVIEW.md' });
  expect(defaultDeliverySelection([])).toBeNull();
});
it('keeps the primary file first and removes transport markers without losing validation results', () => {
  const r = receipt('a'); r.targets.push(r.primary_target!);
  expect(deliveryFiles(r).map((file) => file.path)).toEqual(['index.html', 'report.md']);
  expect(cleanDeliverySummary(r.summary)).toBe('Ready.\n\nChecks passed.');
});


it('finds the previous version of a redelivered file', async () => {
  const { previousDeliveryVersion } = await import('../components/deliveryPresentation');
  const snap = (version: string, stored = true) => [{ path: 'report.md', version: version.repeat(32), size: 1, stored }];
  const v1 = { ...receipt('v1'), delivered_at: 1, snapshots: snap('a') };
  const v2 = { ...receipt('v2'), delivered_at: 2, snapshots: snap('b') };
  const v3 = { ...receipt('v3'), delivered_at: 3, snapshots: snap('b') };
  expect(previousDeliveryVersion(v2, [v3, v2, v1], 'report.md')).toEqual({ path: 'report.md', before: 'a'.repeat(32), after: 'b'.repeat(32) });
  // Identical content is not a change; first delivery has nothing to compare.
  expect(previousDeliveryVersion(v3, [v3, v2, v1], 'report.md')).toEqual({ path: 'report.md', before: 'a'.repeat(32), after: 'b'.repeat(32) });
  expect(previousDeliveryVersion(v1, [v3, v2, v1], 'report.md')).toBeNull();
  expect(previousDeliveryVersion({ ...v2, snapshots: snap('b', false) }, [v2, v1], 'report.md')).toBeNull();
});

it('uses recorded delivery counts, including the first, without inventing missing counts', async () => {
  const { deliveryNumberLabel } = await import('../components/deliveryPresentation');
  const file = receipt('one').primary_target!;
  for (const number of [undefined, 0]) {
    expect(deliveryNumberLabel({ ...file, delivery_number: number }, true)).toBe('');
    expect(deliveryNumberLabel({ ...file, delivery_number: number }, false)).toBe('');
  }
  expect(deliveryNumberLabel({ ...file, delivery_number: 1 }, true)).toBe('第 1 次交付');
  expect(deliveryNumberLabel({ ...file, delivery_number: 2 }, true)).toBe('第 2 次交付');
  expect(deliveryNumberLabel({ ...file, delivery_number: 2 }, false)).toBe('Delivery 2');
});
