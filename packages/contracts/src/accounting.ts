import catalog from '../schemas/model_pricing.json' with { type: 'json' };

/** Wire names match Python's TokenUsage. Missing and reported zero are distinct. */
export interface TokenUsage {
  input_tokens: number;
  cached_input_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  reasoning_output_tokens: number;
  input_tokens_present: boolean;
  cached_input_tokens_present: boolean;
  cache_write_tokens_present: boolean;
  output_tokens_present: boolean;
  reasoning_output_tokens_present: boolean;
  provider_cost_usd: number | null;
  source: 'missing' | 'cumulative' | 'per_message' | 'per_event' | 'pi_message' | 'per_step' | 'provider_request_units';
  observed: boolean;
  complete: boolean;
}

export interface ModelPrice {
  readonly input_usd_per_mtok: number;
  readonly cached_input_usd_per_mtok: number;
  readonly output_usd_per_mtok: number;
  readonly long_context_threshold: number | null;
  readonly long_input_multiplier: number;
  readonly long_cached_input_multiplier: number;
  readonly long_output_multiplier: number;
  readonly cache_write_multiplier: number;
}

export interface PricingQuote {
  cost_usd: number | null;
  status: 'priced' | 'partial' | 'unpriced' | 'not_billed';
  tier: string;
  reason: string;
}

/** An observation/estimate, never a reservation or a settled ledger record. */
export interface RunnerAccounting {
  usage: TokenUsage | null;
  pricing: PricingQuote;
  error: string | null;
}

export const MODEL_PRICES_USD_PER_MTOK: Readonly<Record<string, ModelPrice>> = Object.freeze(
  Object.fromEntries(Object.entries(catalog.models).map(([model, price]) => [model, Object.freeze(price)])),
);
export const COPILOT_USD_PER_PREMIUM_REQUEST = catalog.copilot_usd_per_premium_request;
