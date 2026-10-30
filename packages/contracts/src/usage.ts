import type { PricingQuote } from './accounting.js';
import { isJsonObject } from './schema.js';

/** One call's normalized usage, with no prompts or provider credentials. */
export interface UsageContribution {
  input_tokens: number | null;
  cached_input_tokens: number | null;
  cache_write_tokens: number | null;
  output_tokens: number | null;
  reasoning_output_tokens: number | null;
  total_nano_aiu: number | null;
  cost_usd: number | null;
}

export interface UsageSummaryRecord extends UsageContribution {
  pricing_status: PricingQuote['status'];
  premium_requests: number | null;
  premium_request_cost_usd: number | null;
}

export const USAGE_COUNT_FIELDS = [
  'input_tokens', 'cached_input_tokens', 'cache_write_tokens', 'output_tokens',
  'reasoning_output_tokens', 'total_nano_aiu',
] as const;

const count = (value: unknown): boolean => value === null
  || (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0);
const money = (value: unknown): boolean => value === null
  || (typeof value === 'number' && Number.isFinite(value) && value >= 0);

function requireContribution(value: unknown): void {
  if (!isJsonObject(value) || !USAGE_COUNT_FIELDS.every(key => count(value[key])) || !money(value.cost_usd)) {
    throw new Error('usage contribution requires nonnegative safe counts and finite costs');
  }
}

export function requireUsageSummaryRecord(value: unknown): UsageSummaryRecord {
  requireContribution(value);
  if (!isJsonObject(value) || typeof value.pricing_status !== 'string'
    || !['priced', 'partial', 'unpriced', 'not_billed'].includes(value.pricing_status)
    || !money(value.premium_requests) || !money(value.premium_request_cost_usd)) {
    throw new Error('invalid usage summary record');
  }
  return value as unknown as UsageSummaryRecord;
}
