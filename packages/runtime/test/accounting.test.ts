import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import type { PricingQuote, TokenUsage } from '@argus/contracts';
import {
  copilotUsdPerPremiumRequest, extractTokenUsage, quoteCopilotUsage, quoteTokenUsage,
  TokenUsageAccumulator, UsageAccountingError, type TokenCounts,
} from '../src/index.js';

const read = (name: string): unknown => JSON.parse(readFileSync(new URL(`../fixtures/${name}.json`, import.meta.url), 'utf8'));
const usages = read('token-usage') as Array<{ id: string; events: unknown[] | null; expected: TokenUsage }>;
const quotes = read('pricing') as Array<{ id: string; model: string; counts: TokenCounts; expected: PricingQuote }>;
function equalCost(actual: number | null, expected: number | null): void {
  if (expected === null) assert.equal(actual, null);
  else { assert.notEqual(actual, null); assert.ok(Math.abs(actual! - expected) < 1e-12, `${actual} != ${expected}`); }
}

for (const { id, events, expected } of usages) {
  test(`shared Python/TS token usage: ${id}`, () => {
    const { provider_cost_usd: cost, ...usage } = extractTokenUsage(events);
    const { provider_cost_usd: expectedCost, ...expectedUsage } = expected;
    assert.deepEqual(usage, expectedUsage);
    equalCost(cost, expectedCost);
  });
}
for (const { id, model, counts, expected } of quotes) {
  test(`shared Python/TS pricing: ${id}`, () => {
    const { cost_usd: cost, ...quote } = quoteTokenUsage(model, counts);
    const { cost_usd: expectedCost, ...expectedQuote } = expected;
    assert.deepEqual(quote, expectedQuote);
    equalCost(cost, expectedCost);
  });
}

test('stream snapshots preserve prior receipts and retain constant-sized state', () => {
  const accumulator = new TokenUsageAccumulator();
  accumulator.consume({ data: { inputTokens: 5, outputTokens: 2 } });
  const first = accumulator.snapshot();
  first.input_tokens = 123456;
  const before = JSON.stringify(accumulator).length;
  for (let i = 0; i < 100_000; i++) accumulator.consume({ data: { inputTokens: 1, outputTokens: 2 } });
  const final = accumulator.snapshot();
  assert.equal(final.input_tokens, 100_005);
  assert.equal(final.output_tokens, 200_002);
  assert.ok(JSON.stringify(accumulator).length - before < 40);
  assert.equal(first.output_tokens, 2);
});

test('unsafe counts and sums cannot turn into rounded or zero-cost observations', () => {
  for (const value of [Number.MAX_SAFE_INTEGER + 1, '9007199254740993', Infinity, -Infinity]) {
    assert.throws(() => extractTokenUsage([{ usage: { input_tokens: value } }]), UsageAccountingError);
  }
  const accumulator = new TokenUsageAccumulator();
  accumulator.consume({ data: { inputTokens: Number.MAX_SAFE_INTEGER } });
  assert.throws(() => accumulator.consume({ data: { inputTokens: 1 } }), UsageAccountingError);
  assert.throws(() => accumulator.snapshot(), UsageAccountingError);
  assert.throws(() => accumulator.consume({ usage: { input_tokens: 0, output_tokens: 0 } }), UsageAccountingError);
  assert.throws(() => extractTokenUsage([{
    type: 'message_end', message: { role: 'assistant', usage: { input: Number.MAX_SAFE_INTEGER, cacheRead: 1 } },
  }]), UsageAccountingError);
  assert.throws(() => quoteTokenUsage('gpt-5.5', { input_tokens: Infinity, output_tokens: 1 }), UsageAccountingError);
  assert.throws(() => quoteTokenUsage('gpt-5.5', { input_tokens: 1, output_tokens: Number.MAX_SAFE_INTEGER, reasoning_output_tokens: 1 }), UsageAccountingError);
});

test('premium-request rates preserve zero and reject nonfinite overrides', () => {
  assert.equal(copilotUsdPerPremiumRequest({}), 0.04);
  for (const raw of ['', '-1', 'invalid', 'Infinity', 'NaN', '0x10']) {
    assert.equal(copilotUsdPerPremiumRequest({ ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST: raw }), 0.04);
  }
  assert.equal(copilotUsdPerPremiumRequest({ ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST: ' 0 ' }), 0);
  assert.equal(quoteCopilotUsage(null, 0.04).status, 'partial');
  assert.equal(quoteCopilotUsage(0, 0.04).cost_usd, 0);
  assert.equal(quoteCopilotUsage(2.5, 0.04).cost_usd, 0.1);
  assert.throws(() => quoteCopilotUsage(Infinity), UsageAccountingError);
  assert.throws(() => quoteCopilotUsage(1e308, 1e308), UsageAccountingError);
});
