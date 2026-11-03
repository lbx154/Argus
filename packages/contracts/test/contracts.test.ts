import assert from 'node:assert/strict';
import test from 'node:test';

test('delivery wire types preserve per-file occurrence counts alongside reusable snapshots', () => {
  const target = { path: 'report.md', label: 'report.md', source: 'manager', delivery_number: 3 };
  const delivery: import('../src/models.js').DeliveryReceipt = {
    schema_version: 1, delivery_id: 'd12', kind: 'result', item_id: '', title: 'Report', summary: 'Unchanged content delivered again.',
    status: 'done', review_status: '', delivered_at: 1, primary_target: target, targets: [target],
    snapshots: [{ path: 'report.md', version: 'a'.repeat(32), size: 12, stored: true }],
  };
  const event: import('../src/eventPayloads.generated.js').UiArgusEvent = { type: 'ui.argus', message_id: 'm12-argus', text: delivery.summary, delivery };
  const received = JSON.parse(JSON.stringify(event)) as import('../src/eventPayloads.generated.js').UiArgusEvent;
  assert.equal(received.delivery!.targets![0]!.delivery_number, 3);
  assert.equal(received.delivery!.snapshots![0]!.version, 'a'.repeat(32));
});
