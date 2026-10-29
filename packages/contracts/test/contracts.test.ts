import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { canonicalEventType, EVENT_TYPES, validateEventEnvelope } from '../src/index.js';

interface ValidationCase {
  id: string;
  event: unknown;
  options: { requireKnown?: boolean; allowMissingFields?: boolean };
  expected: { valid: boolean; known: boolean; canonical_type: string };
}
const cases = JSON.parse(readFileSync(new URL('../fixtures/event-validation.json', import.meta.url), 'utf8')) as ValidationCase[];
for (const fixture of cases) {
  test(`shared Python/TS envelope: ${fixture.id}`, () => {
    const { errors, ...actual } = validateEventEnvelope(fixture.event, fixture.options);
    assert.deepEqual(actual, fixture.expected);
    assert.equal(errors.length === 0, actual.valid);
  });
}

test('the complete renderer corpus is recognized and supplied payload fields are checked', () => {
  const corpus = JSON.parse(readFileSync(new URL('../../../frontend/core/fixtures/eventCorpus.generated.json', import.meta.url), 'utf8')) as {
    fixtures: Array<{ id: string; event: unknown }>;
  };
  const types = new Set<string>();
  for (const fixture of corpus.fixtures) {
    const result = validateEventEnvelope(fixture.event, { requireKnown: true, allowMissingFields: true });
    assert.equal(result.valid, true, `${fixture.id}: ${result.errors.join('; ')}`);
    types.add(result.canonical_type);
  }
  assert.deepEqual(types, new Set(Object.values(EVENT_TYPES)));
});

test('non-finite values and inherited alias names cannot cross the boundary', () => {
  for (const number of [NaN, Infinity, -Infinity]) {
    assert.equal(validateEventEnvelope({ type: 'loop.start', ts: number }).valid, false);
  }
  assert.equal(canonicalEventType('__proto__'), '__proto__');
  assert.equal(canonicalEventType('constructor'), 'constructor');
  assert.equal(validateEventEnvelope(null).valid, false);
  assert.equal(validateEventEnvelope([]).valid, false);
});

test('delivery wire types preserve per-file occurrence counts alongside reusable snapshots', () => {
  const target = { path: 'report.md', label: 'report.md', source: 'manager', why: '', delivery_number: 3 };
  const delivery: import('../src/models.js').DeliveryReceipt = {
    schema_version: 1, delivery_id: 'd12', kind: 'result', item_id: '', title: 'Report', summary: 'Unchanged content delivered again.',
    status: 'done', review_status: '', delivered_at: 1, primary_target: target, targets: [target],
    snapshots: [{ path: 'report.md', version: 'a'.repeat(32), size: 12, stored: true }],
  };
  const event: import('../src/eventPayloads.generated.js').UiArgusEvent = { type: 'ui.argus', message_id: 'm12-argus', text: delivery.summary, delivery };
  assert.equal(validateEventEnvelope(event, { requireKnown: true }).valid, true);
  const received = JSON.parse(JSON.stringify(event)) as import('../src/eventPayloads.generated.js').UiArgusEvent;
  assert.equal(received.delivery!.targets![0]!.delivery_number, 3);
  assert.equal(received.delivery!.snapshots![0]!.version, 'a'.repeat(32));
});
