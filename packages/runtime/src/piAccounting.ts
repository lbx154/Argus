import { isJsonObject, type JsonObject, type PricingQuote, type RunnerAccounting } from '@argus/contracts';
import { addCost, UsageAccountingError } from './accountingNumbers.js';
import { quoteObservedUsage } from './pricing.js';
import { extractTokenUsage, TokenUsageAccumulator } from './tokenUsage.js';

const text = (value: unknown): string => typeof value === 'string' ? value.trim() : '';

/** Bounded observations only. Admission, reservations and settlement remain Python. */
export class PiAccountingAccumulator {
  private usage = new TokenUsageAccumulator();
  private error: string | null = null;
  private cost: number | null = null;
  private tier: string | null = null;
  private partial = false;
  private unpriced = false;

  constructor(private readonly model = '', private readonly provider = '') {}

  consume(event: JsonObject): void {
    if (this.error !== null) return;
    try {
      this.usage.consume(event);
      const message = event.message;
      if (text(event.type).toLowerCase() !== 'message_end' || !isJsonObject(message)
        || text(message.role).toLowerCase() !== 'assistant') return;
      const model = text(message.model) || this.model;
      const provider = text(message.provider) || this.provider;
      const qualifiedModel = provider && model && !model.includes('/') ? `${provider}/${model}` : model;
      // The threshold applies to each API turn, not the whole tool conversation.
      const quote = quoteObservedUsage(qualifiedModel, extractTokenUsage([event]));
      if (quote.cost_usd !== null) this.cost = addCost(this.cost ?? 0, quote.cost_usd);
      this.partial ||= quote.status === 'partial';
      this.unpriced ||= quote.status === 'unpriced';
      this.tier = this.tier === null || this.tier === quote.tier ? quote.tier : 'mixed';
    } catch (error) {
      if (!(error instanceof UsageAccountingError)) throw error;
      this.error = error.message;
    }
  }

  snapshot(turnCompleted: boolean): RunnerAccounting {
    if (this.error !== null) return {
      usage: null, error: this.error,
      pricing: { cost_usd: null, status: 'partial', tier: 'unknown', reason: this.error },
    };
    let pricing: PricingQuote;
    if (this.tier === null) pricing = { cost_usd: null, status: 'partial', tier: 'unknown', reason: 'token usage is missing' };
    else {
      const incomplete = !turnCompleted || this.partial || this.unpriced;
      pricing = {
        cost_usd: this.cost, tier: this.tier,
        status: !incomplete ? 'priced' : this.cost === null && this.unpriced && !this.partial && turnCompleted ? 'unpriced' : 'partial',
        reason: !turnCompleted ? 'provider invocation did not settle successfully; observed cost may be incomplete'
          : incomplete ? 'one or more provider turns have missing usage or no configured price' : '',
      };
    }
    return { usage: this.usage.snapshot(), pricing, error: null };
  }
}
