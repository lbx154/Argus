// Route-specific reference prices, never credentials, account balances or a bill.
// A single public metadata fetch is shared; inference never waits for it.
import type { Backend, ModelRates, PriceQuote, Usage } from '../types.js';

const URL = 'https://models.dev/api.json';
const TTL = 5 * 60_000;
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const rate = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0;
export function safeRates(value: unknown): ModelRates | undefined {
  if (!object(value) || !rate(value.input) || !rate(value.output)) return undefined;
  return { input: value.input, output: value.output,
    ...rate(value.cachedInput) ? { cachedInput: value.cachedInput } : {},
    ...rate(value.cacheWrite) ? { cacheWrite: value.cacheWrite } : {} };
}

export class PriceBook {
  private native = new Map<string, PriceQuote>();
  private reference = new Map<string, ModelRates>();
  private checked = 0;
  private attempted = 0;
  private pending: Promise<void> | undefined;
  constructor(private readonly fetcher: typeof fetch = fetch, private readonly clock = Date.now) {}
  private key(backend: Backend, cwd: string, model: string) { return JSON.stringify([backend, cwd, model]); }
  remember(backend: Backend, cwd: string, model: string, values: unknown): void {
    const rates = safeRates(values);
    const key = this.key(backend, cwd, model);
    if (!rates) { this.native.delete(key); return; }
    if (this.native.size >= 4096) this.native.delete(this.native.keys().next().value!);
    this.native.set(key, { source: 'cli-catalog', checked_at: new Date(this.clock()).toISOString(), rates, stale: false });
  }
  /** Only bounded public USD/1M metadata is retained; unknown fields never cross the API. */
  async refresh(force = false): Promise<void> {
    if (this.pending) return this.pending;
    if (!force && this.attempted && this.clock() - this.attempted < (this.checked ? TTL : 60_000)) return;
    this.attempted = this.clock();
    const operation = async () => {
      try {
        const response = await this.fetcher(URL, { signal: AbortSignal.timeout(5000), redirect: 'error', credentials: 'omit' });
        if (!response.ok || !response.body) return;
        const reader = response.body.getReader(); const chunks: Uint8Array[] = []; let size = 0;
        try {
          for (;;) { const { done, value } = await reader.read(); if (done) break;
            size += value.byteLength; if (size > 16 * 1024 * 1024) return; chunks.push(value); }
        } finally { await reader.cancel().catch(() => {}); }
        const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if (!object(parsed)) return;
        const next = new Map<string, ModelRates>();
        for (const [provider, entry] of Object.entries(parsed)) {
          if (!object(entry) || !object(entry.models)) continue;
          for (const [id, model] of Object.entries(entry.models)) {
            if (!object(model) || !object(model.cost)) continue;
            const rates = safeRates({ input: model.cost.input, output: model.cost.output,
              cachedInput: model.cost.cache_read, cacheWrite: model.cost.cache_write });
            // Tiered prices cannot be represented by one unit rate. Fail closed.
            if (rates && !model.cost.context_over_200k) next.set(`${provider}/${id}`, rates);
          }
        }
        if (next.size) { this.reference = next; this.checked = this.clock(); }
      } catch { /* Offline means stale/unknown, never invented zero or a blocked inference. */ }
    };
    this.pending = operation();
    try { await this.pending; } finally { this.pending = undefined; }
  }
  quote(backend: Backend, cwd: string, model: string | null): PriceQuote {
    const native = this.native.get(this.key(backend, cwd, model ?? ''));
    if (native) return { ...native, rates: native.rates && { ...native.rates }, stale: this.clock() - Date.parse(native.checked_at!) >= TTL };
    // Resolve only exact public IDs; never guess what sonnet/default/flash aliases mean.
    let id = model ?? '';
    if (id.startsWith('deepseek-official/')) id = 'deepseek/' + id.slice('deepseek-official/'.length);
    if (!id.includes('/')) {
      const provider = backend === 'codex' ? 'openai' : backend === 'claude' ? 'anthropic' : backend === 'grok' ? 'xai' : '';
      id = provider ? `${provider}/${id}` : '';
    }
    const rates = this.reference.get(id);
    return rates ? { source: 'public-reference', reference: URL, checked_at: new Date(this.checked).toISOString(),
      rates: { ...rates }, stale: this.clock() - this.checked >= TTL }
      : { source: 'unknown', checked_at: null, rates: null, stale: false };
  }
}
export const priceBook = new PriceBook();

/** Counts already include cache reads/writes; reasoning is already in output. */
export function estimateUsage(usage: Usage, quote: PriceQuote): number | null {
  const rates = quote.rates;
  if (!rates || quote.stale) return null;
  const { input_tokens: input, output_tokens: output, cached_input_tokens: cached, cache_write_tokens: writes = 0 } = usage;
  if (![input, output, cached, writes].every(rate) || cached + writes > input || input > 128_000) return null;
  if (cached && rates.cachedInput === undefined || writes && rates.cacheWrite === undefined) return null;
  const cost = ((input - cached - writes) * rates.input + output * rates.output
    + cached * (rates.cachedInput ?? 0) + writes * (rates.cacheWrite ?? 0)) / 1_000_000;
  return Number.isFinite(cost) ? cost : null;
}
