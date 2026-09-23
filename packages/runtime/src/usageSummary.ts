import {
  USAGE_COUNT_FIELDS, requireUsageSummaryRecord, type UsageContribution,
  type UsageSummary, type UsageSummaryRecord,
} from '@argus/contracts';
import { addCost, exactCount, UsageAccountingError } from './accountingNumbers.js';

export interface UsageSummaryLimits {
  maxRecords?: number;
  maxIdentities?: number;
}

/** Port of summarize_usage; preserves record counts while deduplicating model rows.
 * Holds only totals and bounded Copilot identities, never the input records.
 */
export class UsageSummaryAccumulator {
  private readonly maxRecords: number;
  private readonly maxIdentities: number;
  private readonly seen = new Set<string>();
  private failure: UsageAccountingError | null = null;
  private costPresent = false;
  private readonly totals: UsageSummary = {
    call_count: 0, known_cost_usd: 0, cost_usd: null, pricing_status: 'empty',
    priced_calls: 0, partial_calls: 0, unpriced_calls: 0, not_billed_calls: 0,
    input_tokens: 0, cached_input_tokens: 0, cache_write_tokens: 0, output_tokens: 0,
    reasoning_output_tokens: 0, total_nano_aiu: 0, premium_requests: 0, premium_request_cost_usd: 0,
  };

  constructor(limits: UsageSummaryLimits = {}) {
    this.maxRecords = limits.maxRecords ?? 100_000;
    this.maxIdentities = limits.maxIdentities ?? 100_000;
    if (![this.maxRecords, this.maxIdentities].every(value => Number.isSafeInteger(value) && value > 0)) {
      throw new Error('summary limits must be positive safe integers');
    }
  }

  add(value: unknown): void {
    if (this.failure) throw this.failure;
    try {
      const row = requireUsageSummaryRecord(value);
      if (this.totals.call_count >= this.maxRecords) throw new UsageAccountingError('Usage summary record limit exceeded.');
      this.totals.call_count += 1;
      const key = { priced: 'priced_calls', partial: 'partial_calls', unpriced: 'unpriced_calls', not_billed: 'not_billed_calls' } as const;
      this.totals[key[row.pricing_status]] += 1;
      this.totals.premium_requests = addCost(this.totals.premium_requests, row.premium_requests ?? 0);
      this.totals.premium_request_cost_usd = addCost(this.totals.premium_request_cost_usd, row.premium_request_cost_usd ?? 0);
      if (row.model_usage.length === 0) this.contribute(row);
      else for (const item of row.model_usage) {
        if (item.session_id !== null && item.usage_event_id !== null) {
          const identity = JSON.stringify([item.session_id, item.usage_event_id]);
          if (this.seen.has(identity)) continue;
          if (this.seen.size >= this.maxIdentities) throw new UsageAccountingError('Usage summary identity limit exceeded.');
          this.seen.add(identity);
        }
        this.contribute(item);
      }
    } catch (error) {
      this.failure = error instanceof UsageAccountingError ? error : new UsageAccountingError('Invalid normalized usage record.');
      throw this.failure;
    }
  }

  private contribute(row: UsageContribution): void {
    for (const key of USAGE_COUNT_FIELDS) this.totals[key] = exactCount(this.totals[key] + (row[key] ?? 0));
    if (row.cost_usd !== null) {
      this.costPresent = true;
      this.totals.known_cost_usd = addCost(this.totals.known_cost_usd, row.cost_usd);
    }
  }

  snapshot(): UsageSummary {
    if (this.failure) throw this.failure;
    const totals = this.totals;
    const status = totals.partial_calls ? 'partial' : totals.unpriced_calls ? 'unpriced'
      : totals.call_count && totals.not_billed_calls === totals.call_count ? 'not_billed'
      : totals.call_count ? 'priced' : 'empty';
    const incompleteZero = (status === 'partial' || status === 'unpriced') && totals.known_cost_usd <= 0;
    return { ...totals, pricing_status: status,
      cost_usd: this.costPresent && !incompleteZero ? totals.known_cost_usd : null };
  }
}

export function summarizeUsage(records: Iterable<UsageSummaryRecord>, limits?: UsageSummaryLimits): UsageSummary {
  const accumulator = new UsageSummaryAccumulator(limits);
  for (const record of records) accumulator.add(record);
  return accumulator.snapshot();
}
