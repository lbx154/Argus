import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import type { DeliveryReceipt } from '../../../core/src/types';
import { useDeliveryCenter } from '../useDeliveryCenter';
import { cleanDeliverySummary, deliveryFiles, selectActiveDelivery, hasPendingDeliveryDependents } from '../components/deliveryPresentation';
const receipt = (id: string): DeliveryReceipt => ({ schema_version: 1, delivery_id: id, item_id: id, kind: 'task_completed', status: 'done', review_status: 'done', title: id, summary: 'Ready. RESULT=Checks passed.', delivered_at: 1, primary_target: { path: 'index.html', label: 'Website', source: 'delivery', why: 'Reviewed' }, targets: [{ path: 'report.md', label: 'Report', source: 'delivery', why: 'Reviewed' }] });
afterEach(() => vi.unstubAllGlobals());
it('opens a new delivery once, keeps files selectable, and does not replay historical receipts', () => {
  const stored = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (k: string) => stored.get(k), setItem: (k: string, v: string) => stored.set(k, v) });
  let center!: ReturnType<typeof useDeliveryCenter>;
  function Probe({ sid, delivery, ready = true }: { sid: string; delivery: DeliveryReceipt | null; ready?: boolean }) { center = useDeliveryCenter(sid, ready, delivery); return null; }
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(<Probe sid="a" delivery={receipt('old')} />); });
  expect(center.selection).toBeNull();
  act(() => renderer.update(<Probe sid="a" delivery={receipt('new')} />));
  expect(center.selection?.receipt.delivery_id).toBe('new');
  act(() => center.selectPath('report.md'));
  expect(center.selection?.path).toBe('report.md');
  act(() => center.close());
  act(() => renderer.update(<Probe sid="a" delivery={null} />));
  act(() => renderer.update(<Probe sid="a" delivery={receipt('new')} />));
  expect(center.selection).toBeNull();
  act(() => center.open(receipt('old')));
  expect(center.selection?.path).toBe('index.html');
  act(() => renderer.update(<Probe sid="b" delivery={receipt('other')} />));
  expect(center.selection).toBeNull();
  act(() => renderer.update(<Probe sid="a" delivery={null} />));
  act(() => renderer.update(<Probe sid="a" delivery={receipt('new')} />));
  expect(center.selection).toBeNull(); // persisted across session changes
  act(() => renderer.unmount());
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

it('keeps intermediate deliveries accessible and opens once their downstream work is finished', () => {
  let center!: ReturnType<typeof useDeliveryCenter>;
  function Probe({ delivery, pending }: { delivery: DeliveryReceipt | null; pending: boolean }) { center = useDeliveryCenter('pipeline', true, delivery, !pending); return null; }
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(<Probe delivery={null} pending />); });
  act(() => renderer.update(<Probe delivery={receipt('data')} pending />));
  expect(center.selection).toBeNull();
  act(() => center.open(receipt('data')));
  expect(center.selection?.receipt.delivery_id).toBe('data');
  act(() => center.close());
  act(() => renderer.update(<Probe delivery={receipt('final')} pending={false} />));
  expect(center.selection?.receipt.delivery_id).toBe('final');
  act(() => renderer.unmount());
  const item = (id: string, status: string, deps: string[] = []) => ({ id, status, deps, title: id, objective: '', priority: 0 });
  const tasks = [item('a', 'done'), item('b', 'done', ['a']), item('c', 'running', ['b']), item('other', 'running')];
  expect(hasPendingDeliveryDependents(tasks, 'a')).toBe(true);
  expect(hasPendingDeliveryDependents(tasks.map((task) => task.id === 'c' ? { ...task, status: 'done' } : task), 'a')).toBe(false);
});
