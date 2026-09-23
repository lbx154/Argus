import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import type { UsageSummary, UsageSummaryRecord } from '@argus/contracts';
import { UsageAccountingError, UsageSummaryAccumulator, summarizeUsage } from '../src/index.js';

const cases = JSON.parse(readFileSync(new URL('../fixtures/usage-summary.json', import.meta.url), 'utf8')) as Array<{
  id: string; records: UsageSummaryRecord[]; expected: UsageSummary;
}>;
const base = cases.find(row => row.id === 'one-priced')!.records[0]!;
const withModel = cases.find(row => row.id === 'model-detail-replaces-aggregate')!.records[0]!;

for (const fixture of cases) {
  test(`shared Python/TS usage summary: ${fixture.id}`, () => {
    const actual = summarizeUsage(fixture.records);
    for (const key of Object.keys(fixture.expected) as Array<keyof UsageSummary>) {
      const expected = fixture.expected[key];
      if (typeof expected === 'number' && typeof actual[key] === 'number') {
        assert.ok(Math.abs(actual[key] - expected) <= Math.max(1e-12, Math.abs(expected) * 1e-12), key);
      } else assert.equal(actual[key], expected, key);
    }
  });
}

test('summary snapshots and separate projects cannot share mutable totals or identities', () => {
  const accumulator = new UsageSummaryAccumulator();
  accumulator.add(withModel);
  const first = accumulator.snapshot();
  first.input_tokens = 999;
  accumulator.add(withModel);
  assert.equal(accumulator.snapshot().input_tokens, 80);
  assert.equal(accumulator.snapshot().call_count, 2);
  assert.equal(summarizeUsage([withModel]).input_tokens, 80);
});

test('invalid normalized records latch failure instead of yielding partial success', () => {
  for (const invalid of [null, {}, { ...base, cost_usd: -1 }, { ...base, input_tokens: true },
    { ...base, pricing_status: 'unknown' }, { ...base, premium_requests: Infinity },
    { ...base, input_tokens: Number.MAX_SAFE_INTEGER + 1 }, { ...base, output_tokens: 1.5 },
    { ...withModel, model_usage: [{ ...withModel.model_usage[0], usage_event_id: Number.MAX_SAFE_INTEGER + 1 }] },
  ]) {
    const accumulator = new UsageSummaryAccumulator();
    accumulator.add(base);
    assert.throws(() => accumulator.add(invalid), UsageAccountingError);
    assert.throws(() => accumulator.snapshot(), UsageAccountingError);
    assert.throws(() => accumulator.add(base), UsageAccountingError);
  }
});

test('token/nano-AIU and cost overflow are explicit errors', () => {
  for (const key of ['input_tokens', 'total_nano_aiu'] as const) {
    assert.throws(() => summarizeUsage([{ ...base, [key]: Number.MAX_SAFE_INTEGER }, { ...base, [key]: 1 }]), UsageAccountingError);
  }
  assert.throws(() => summarizeUsage([{ ...base, cost_usd: 1e308 }, { ...base, cost_usd: 1e308 }]), UsageAccountingError);
});

test('record and identity bounds do not evict duplicates and silently overcharge', () => {
  assert.throws(() => summarizeUsage([base, base], { maxRecords: 1 }), /record limit/);
  const accumulator = new UsageSummaryAccumulator({ maxIdentities: 1 });
  accumulator.add(withModel);
  accumulator.add(withModel);
  assert.equal(accumulator.snapshot().known_cost_usd, 0.01);
  assert.throws(() => accumulator.add({ ...withModel,
    model_usage: [{ ...withModel.model_usage[0], session_id: 'other' }],
  }), /identity limit/);
  assert.throws(() => accumulator.snapshot(), UsageAccountingError);
});
