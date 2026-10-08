import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import type { DeliveryReceipt } from '../../../core/src/types';
import { useDeliveryCenter } from '../useDeliveryCenter';
import { cleanDeliverySummary, deliveryFiles, defaultDeliverySelection, selectActiveDelivery, hasPendingDeliveryDependents } from '../components/deliveryPresentation';
const receipt = (id: string): DeliveryReceipt => ({ schema_version: 1, delivery_id: id, item_id: id, kind: 'task_completed', status: 'done', review_status: 'done', title: id, summary: 'Ready. RESULT=Checks passed.', delivered_at: 1, primary_target: { path: 'index.html', label: 'Website', source: 'delivery', why: 'Reviewed' }, targets: [{ path: 'report.md', label: 'Report', source: 'delivery', why: 'Reviewed' }] });
afterEach(() => vi.unstubAllGlobals());
it('announces a new delivery without opening the viewer, keeps files selectable, and does not replay historical receipts', () => {
  const stored = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (k: string) => stored.get(k), setItem: (k: string, v: string) => stored.set(k, v) });
  let center!: ReturnType<typeof useDeliveryCenter>;
  function Probe({ sid, delivery, ready = true }: { sid: string; delivery: DeliveryReceipt | null; ready?: boolean }) { center = useDeliveryCenter(sid, ready, delivery); return null; }
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(<Probe sid="a" delivery={receipt('old')} />); });
  expect(center.selection).toBeNull();
  act(() => renderer.update(<Probe sid="a" delivery={receipt('new')} />));
  // A fresh receipt is announced, not forced full-screen over the reply/draft.
  expect(center.selection).toBeNull();
  expect(center.fresh?.delivery_id).toBe('new');
  act(() => center.open(center.fresh!));
  expect(center.fresh).toBeNull();
  expect(center.selection?.receipt.delivery_id).toBe('new');
  act(() => center.selectPath('report.md'));
  expect(center.selection?.path).toBe('report.md');
  act(() => center.close());
  act(() => renderer.update(<Probe sid="a" delivery={null} />));
  act(() => renderer.update(<Probe sid="a" delivery={receipt('new')} />));
  expect(center.selection).toBeNull();
  act(() => center.open(receipt('old')));
  expect(center.selection?.path).toBe('index.html');
  act(() => center.open(receipt('old'), 'report.md'));
  expect(center.selection?.path).toBe('report.md');
  act(() => center.open(receipt('old'), 'missing.pdf'));
  expect(center.selection?.path).toBe('index.html');
  act(() => renderer.update(<Probe sid="b" delivery={receipt('other')} />));
  expect(center.selection).toBeNull();
  act(() => renderer.update(<Probe sid="a" delivery={null} />));
  act(() => renderer.update(<Probe sid="a" delivery={receipt('new')} />));
  expect(center.selection).toBeNull(); // persisted across session changes
  act(() => renderer.unmount());
});

it('opens the research manuscript after a newer task delivered only its review report', () => {
  const report = { path: 'paper/REVIEW.md', label: 'Review', source: 'delivery', why: 'Review report' };
  const latest = { ...receipt('review'), primary_target: report, targets: [] };
  const paper = { ...receipt('paper'), primary_target: report, targets: [{ ...report, path: 'paper/main.pdf', label: 'Paper' }] };
  expect(defaultDeliverySelection([latest, paper], 'research')).toEqual({ receipt: paper, path: 'paper/main.pdf' });
  expect(defaultDeliverySelection([latest, paper], 'software')).toEqual({ receipt: latest, path: 'paper/REVIEW.md' });
  expect(defaultDeliverySelection([latest], 'research')).toEqual({ receipt: latest, path: 'paper/REVIEW.md' });
  expect(defaultDeliverySelection([], 'research')).toBeNull();
});
it('waits for initial transcript hydration and ignores completions without files', () => {
  let center!: ReturnType<typeof useDeliveryCenter>;
  function Probe({ delivery, ready }: { delivery: DeliveryReceipt | null; ready: boolean }) { center = useDeliveryCenter('a', ready, delivery); return null; }
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(<Probe ready={false} delivery={null} />); });
  act(() => renderer.update(<Probe ready delivery={receipt('history')} />));
  expect(center.selection).toBeNull();
  act(() => renderer.update(<Probe ready delivery={{ ...receipt('chat'), primary_target: null, targets: [] }} />));
  expect(center.selection).toBeNull();
  act(() => renderer.unmount());
});
it('keeps the primary file first and removes transport markers without losing validation results', () => {
  const r = receipt('a'); r.targets.push(r.primary_target!);
  expect(deliveryFiles(r).map((file) => file.path)).toEqual(['index.html', 'report.md']);
  expect(cleanDeliverySummary(r.summary)).toBe('Ready.\n\nChecks passed.');
});

it('surfaces async task completion after an operator turn, while keeping old deliveries in history', () => {
  const events = [{ type: 'ui.operator', ts: 10, text: 'Build it' }, { type: 'ui.argus', ts: 11, text: 'Started' }];
  expect(selectActiveDelivery(null, { ...receipt('new'), delivered_at: 12 }, events)?.delivery_id).toBe('new');
  expect(selectActiveDelivery(null, { ...receipt('old'), delivered_at: 9 }, events)).toBeNull();
  expect(selectActiveDelivery({ ...receipt('solo'), delivered_at: 13 }, { ...receipt('task'), delivered_at: 12 }, events)?.delivery_id).toBe('solo');
});

it('keeps intermediate deliveries accessible and announces once their downstream work is finished', () => {
  let center!: ReturnType<typeof useDeliveryCenter>;
  function Probe({ delivery, pending }: { delivery: DeliveryReceipt | null; pending: boolean }) { center = useDeliveryCenter('pipeline', true, delivery, !pending); return null; }
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(<Probe delivery={null} pending />); });
  act(() => renderer.update(<Probe delivery={receipt('data')} pending />));
  expect(center.selection).toBeNull();
  act(() => center.open(receipt('data')));
  expect(center.selection?.receipt.delivery_id).toBe('data');
  act(() => center.close());
  expect(center.fresh).toBeNull();
  act(() => renderer.update(<Probe delivery={receipt('final')} pending={false} />));
  expect(center.selection).toBeNull();
  expect(center.fresh?.delivery_id).toBe('final');
  act(() => center.dismissFresh());
  expect(center.fresh).toBeNull();
  act(() => renderer.unmount());
  const item = (id: string, status: string, deps: string[] = []) => ({ id, status, deps, title: id, objective: '', priority: 0 });
  const tasks = [item('a', 'done'), item('b', 'done', ['a']), item('c', 'running', ['b']), item('other', 'running')];
  expect(hasPendingDeliveryDependents(tasks, 'a')).toBe(true);
  expect(hasPendingDeliveryDependents(tasks.map((task) => task.id === 'c' ? { ...task, status: 'done' } : task), 'a')).toBe(false);
});

it('labels who checked a delivery and finds the previous version of a redelivered file', async () => {
  const { deliveryReviewLabel, previousDeliveryVersion } = await import('../components/deliveryPresentation');
  const reviewed = deliveryReviewLabel({ ...receipt('r'), review_status: 'done', review_source: 'reviewer' }, true);
  expect(reviewed.independent).toBe(true);
  expect(reviewed.text).toBe('独立复核通过');
  // A worker that settles its own task as done has not been independently reviewed.
  const selfDone = deliveryReviewLabel({ ...receipt('e'), review_status: 'done', review_source: 'engineer_self_review' }, false);
  expect(selfDone.independent).toBe(false);
  expect(selfDone.text).toMatch(/not independently reviewed/i);
  // Older receipts never recorded who checked them; do not claim independence.
  const legacy = deliveryReviewLabel({ ...receipt('l'), review_status: 'done' }, false);
  expect(legacy.independent).toBe(false);
  expect(legacy.text).toMatch(/not recorded/i);
  const self = deliveryReviewLabel({ ...receipt('s'), review_status: 'not_assessed' }, false);
  expect(self.independent).toBe(false);
  expect(self.text).toMatch(/self-check/i);
  const snap = (sha: string, stored = true) => [{ path: 'report.md', sha256: sha.repeat(64), size: 1, stored }];
  const v1 = { ...receipt('v1'), delivered_at: 1, snapshots: snap('a') };
  const v2 = { ...receipt('v2'), delivered_at: 2, snapshots: snap('b') };
  const v3 = { ...receipt('v3'), delivered_at: 3, snapshots: snap('b') };
  expect(previousDeliveryVersion(v2, [v3, v2, v1], 'report.md')).toEqual({ path: 'report.md', before: 'a'.repeat(64), after: 'b'.repeat(64) });
  // Identical content is not a change; first delivery has nothing to compare.
  expect(previousDeliveryVersion(v3, [v3, v2, v1], 'report.md')).toEqual({ path: 'report.md', before: 'a'.repeat(64), after: 'b'.repeat(64) });
  expect(previousDeliveryVersion(v1, [v3, v2, v1], 'report.md')).toBeNull();
  expect(previousDeliveryVersion({ ...v2, snapshots: snap('b', false) }, [v2, v1], 'report.md')).toBeNull();
});
