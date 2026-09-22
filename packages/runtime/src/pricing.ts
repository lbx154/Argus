import {
  COPILOT_USD_PER_PREMIUM_REQUEST, MODEL_PRICES_USD_PER_MTOK,
  type ModelPrice, type PricingQuote, type TokenUsage,
} from '@argus/contracts';
import { addCost, exactCount, nonnegativeFloat, UsageAccountingError } from './accountingNumbers.js';

/** Keep Python's exact-then-prefix lookup order; never invent an unknown price. */
export function modelPriceFor(model: string): ModelPrice | null {
  let normalized = model.trim().toLowerCase();
  if (normalized.startsWith('openai/')) normalized = normalized.slice('openai/'.length);
  if (Object.hasOwn(MODEL_PRICES_USD_PER_MTOK, normalized)) return MODEL_PRICES_USD_PER_MTOK[normalized]!;
  for (const [name, price] of Object.entries(MODEL_PRICES_USD_PER_MTOK)) {
    if (normalized.startsWith(`${name}-`)) return price;
  }
  return null;
}

export interface TokenCounts {
  input_tokens: number | null;
  output_tokens: number | null;
  cached_input_tokens?: number | null;
  cache_write_tokens?: number | null;
  reasoning_output_tokens?: number | null;
}

export function quoteTokenUsage(model: string, usage: TokenCounts): PricingQuote {
  // Validate before selecting a price: unknown models must not hide corrupt counts.
  const count = (value: number | null | undefined): number => {
    if (value == null) return 0;
    if (!Number.isFinite(value)) throw new UsageAccountingError('Token count must be finite.');
    return exactCount(Math.max(0, Math.trunc(value)));
  };
  const input = count(usage.input_tokens);
  const cached = Math.min(count(usage.cached_input_tokens), input);
  const writes = Math.min(count(usage.cache_write_tokens), input - cached);
  const output = count(usage.output_tokens);
  const reasoning = count(usage.reasoning_output_tokens);
  const price = modelPriceFor(model);
  if (!price) return { cost_usd: null, status: 'unpriced', tier: 'unknown', reason: `no configured price for model ${model || '(missing)'}` };
  if (usage.input_tokens == null && usage.output_tokens == null) {
    return { cost_usd: null, status: 'partial', tier: 'unknown', reason: 'token usage is missing' };
  }
  if (usage.input_tokens == null && price.long_context_threshold !== null) {
    return { cost_usd: null, status: 'partial', tier: 'unknown', reason: 'input tokens missing; long-context tier cannot be selected' };
  }
  const long = price.long_context_threshold !== null && usage.input_tokens != null && input > price.long_context_threshold;
  const inputMultiplier = long ? price.long_input_multiplier : 1;
  const cachedMultiplier = long ? price.long_cached_input_multiplier : 1;
  const outputMultiplier = long ? price.long_output_multiplier : 1;
  const cost = (
    (input - cached - writes) * price.input_usd_per_mtok * inputMultiplier
    + cached * price.cached_input_usd_per_mtok * cachedMultiplier
    + writes * price.input_usd_per_mtok * inputMultiplier * price.cache_write_multiplier
    + exactCount(output + reasoning) * price.output_usd_per_mtok * outputMultiplier
  ) / 1_000_000;
  const complete = usage.input_tokens != null && usage.output_tokens != null;
  return { cost_usd: addCost(0, cost), status: complete ? 'priced' : 'partial', tier: long ? 'long_context' : 'default',
    reason: complete ? '' : 'input or output token count is missing' };
}

/** Explicit provider totals take precedence over reference-price estimates. */
export function quoteObservedUsage(model: string, usage: TokenUsage): PricingQuote {
  if (usage.provider_cost_usd !== null) {
    const cost = nonnegativeFloat(usage.provider_cost_usd);
    if (cost === null) throw new UsageAccountingError('Provider cost must be finite and nonnegative.');
    return { cost_usd: cost, status: 'priced', tier: 'provider_reported', reason: '' };
  }
  return quoteTokenUsage(model, {
    input_tokens: usage.input_tokens_present ? usage.input_tokens : null,
    cached_input_tokens: usage.cached_input_tokens_present ? usage.cached_input_tokens : null,
    cache_write_tokens: usage.cache_write_tokens_present ? usage.cache_write_tokens : null,
    output_tokens: usage.output_tokens_present ? usage.output_tokens : null,
    reasoning_output_tokens: usage.reasoning_output_tokens_present ? usage.reasoning_output_tokens : null,
  });
}

export function copilotUsdPerPremiumRequest(env: NodeJS.ProcessEnv = process.env): number {
  return nonnegativeFloat(env.ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST) ?? COPILOT_USD_PER_PREMIUM_REQUEST;
}

/** Legacy premium-request pricing only; modern Copilot AIU settlement stays Python. */
export function quoteCopilotUsage(requests: number | null, unitPrice = copilotUsdPerPremiumRequest()): PricingQuote {
  if (requests === null) return { cost_usd: null, status: 'partial', tier: 'premium_request', reason: 'Copilot premium-request usage is missing' };
  if (!Number.isFinite(requests) || nonnegativeFloat(unitPrice) === null) throw new UsageAccountingError('Premium-request pricing must be finite and nonnegative.');
  return { cost_usd: addCost(0, Math.max(0, requests) * unitPrice), status: 'priced', tier: 'premium_request', reason: '' };
}
