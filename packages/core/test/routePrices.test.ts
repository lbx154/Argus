import assert from 'node:assert/strict';
import test from 'node:test';
import { PriceBook, estimateUsage, safeRates } from '../src/agent/prices.js';
import type { Usage } from '../src/types.js';
const catalog = {
  openai: { models: { same: { cost: { input: 2, output: 8, cache_read: .2, cache_write: 3 } } } },
  anthropic: { models: { same: { cost: { input: 5, output: 20, cache_read: .5, cache_write: 6 } } } },
  deepseek: { models: { 'deepseek-v4-pro': { cost: { input: 3, output: 7, cache_read: .3 } } } },
};
const fetcher = async () => Response.json(catalog);
const usage: Usage = { input_tokens: 1000, cached_input_tokens: 200, cache_write_tokens: 100,
  output_tokens: 50, reasoning_output_tokens: 20, cost_usd: null };

test('four roles on four CLI/provider routes cannot share a model-name-only price', async () => {
  const book = new PriceBook(fetcher);
  await book.refresh();
  book.remember('pi', 'work', 'custom/same', { input: 11, output: 12, cachedInput: 1, cacheWrite: 2 });
  book.remember('dsh', 'work', 'custom/same', { input: 21, output: 22, cachedInput: 2, cacheWrite: 3 });
  const quotes = [book.quote('codex', 'work', 'same'), book.quote('claude', 'work', 'same'),
    book.quote('pi', 'work', 'custom/same'), book.quote('dsh', 'work', 'custom/same')];
  assert.deepEqual(quotes.map(q => q.rates?.input), [2, 5, 11, 21]);
  assert.equal(new Set(quotes.map(q => estimateUsage(usage, q))).size, 4);
  assert.equal(book.quote('pi', 'different-work', 'custom/same').source, 'unknown');
});

test('cache usage is disjoint for pricing, and reasoning is not charged twice', async () => {
  const book = new PriceBook(fetcher); await book.refresh();
  const quote = book.quote('codex', '', 'same');
  assert.equal(estimateUsage(usage, quote), (700 * 2 + 200 * .2 + 100 * 3 + 50 * 8) / 1e6);
  assert.equal(estimateUsage({ ...usage, reasoning_output_tokens: 500 }, quote), estimateUsage(usage, quote));
});

test('missing cache prices, stale rates, huge aggregated context and unknown aliases stay unpriced', async () => {
  const book = new PriceBook(fetcher); await book.refresh();
  const unknown = book.quote('claude', '', 'sonnet');
  assert.equal(unknown.source, 'unknown');
  assert.equal(book.quote('dsh', '', 'deepseek-official/deepseek-flash').source, 'unknown');
  assert.equal(book.quote('dsh', '', 'deepseek-official/deepseek-v4-pro').source, 'public-reference');
  assert.equal(estimateUsage(usage, unknown), null);
  assert.equal(estimateUsage(usage, { ...unknown, rates: { input: 1, output: 2 } }), null);
  const quote = book.quote('codex', '', 'same');
  assert.equal(estimateUsage(usage, { ...quote, stale: true }), null);
  assert.equal(estimateUsage({ ...usage, input_tokens: 200_000 }, quote), null);
  assert.equal(estimateUsage({ ...usage, cached_input_tokens: 1001 }, quote), null);
});

test('only finite public rate fields are projected, zero stays distinct from missing', () => {
  assert.deepEqual(safeRates({ input: 0, output: 0, headers: { authorization: 'synthetic-not-a-credential' }, url: 'https://example.invalid' }), { input: 0, output: 0 });
  for (const input of [-1, NaN, Infinity, '2', undefined]) assert.equal(safeRates({ input, output: 1 }), undefined);
});

test('refresh is deduplicated and cached; failures preserve stale evidence, never rewrite snapshots', async () => {
  let now = 1_000_000, calls = 0, fail = false;
  const book = new PriceBook(async () => { calls++; if (fail) throw Error('offline'); return Response.json(catalog); }, () => now);
  await Promise.all([book.refresh(), book.refresh(true), book.refresh()]); assert.equal(calls, 1);
  const captured = book.quote('codex', '', 'same');
  await book.refresh(); assert.equal(calls, 1);
  now += 300_001; fail = true; await book.refresh();
  assert.equal(book.quote('codex', '', 'same').stale, true);
  assert.equal(captured.stale, false);
});

test('native quote snapshots are immutable and can be invalidated by a new catalog', () => {
  const book = new PriceBook(fetcher);
  book.remember('pi', '', 'a/b', { input: 1, output: 2 });
  const old = book.quote('pi', '', 'a/b'); old.rates!.input = 99;
  assert.equal(book.quote('pi', '', 'a/b').rates?.input, 1);
  book.remember('pi', '', 'a/b', undefined);
  assert.equal(book.quote('pi', '', 'a/b').source, 'unknown');
});

test('public fetch sends no credentials, rejects redirects and ignores tiered or invalid costs', async () => {
  const book = new PriceBook(async (url, options) => {
    assert.equal(url, 'https://models.dev/api.json'); assert.equal(options?.credentials, 'omit'); assert.equal(options?.redirect, 'error');
    return Response.json({ openai: { models: { same: { cost: { input: 2, output: 3, context_over_200k: { input: 4 } } }, bad: { cost: { input: -2, output: 3 } } } } });
  });
  await book.refresh();
  assert.equal(book.quote('codex', '', 'same').source, 'unknown');
  assert.equal(book.quote('codex', '', 'bad').source, 'unknown');
});
