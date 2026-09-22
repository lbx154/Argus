import { isJsonObject, type JsonObject, type TokenUsage } from '@argus/contracts';
import { addCost, exactCount, nonnegativeFloat, tokenCount, UsageAccountingError } from './accountingNumbers.js';

const indices = [0, 1, 2, 3, 4] as const;
type Index = typeof indices[number];
type Five<T> = [T, T, T, T, T];
const aliases: Five<readonly string[]> = [
  ['input_tokens', 'prompt_tokens', 'inputTokens', 'promptTokens'],
  ['cached_input_tokens', 'cache_read_input_tokens', 'cachedInputTokens', 'cacheReadInputTokens'],
  ['cache_write_tokens', 'cache_creation_input_tokens', 'cacheWriteTokens', 'cacheCreationInputTokens'],
  ['output_tokens', 'completion_tokens', 'outputTokens', 'completionTokens'],
  ['reasoning_output_tokens', 'reasoning_tokens', 'reasoningOutputTokens', 'reasoningTokens'],
];
const object = (value: unknown): JsonObject => isJsonObject(value) ? value : {};
const truthy = (value: unknown): boolean => Boolean(value)
  && !(Array.isArray(value) && value.length === 0)
  && !(isJsonObject(value) && Object.keys(value).length === 0);
const text = (value: unknown): string => String(truthy(value) ? value : '').trim().toLowerCase();

class Counts {
  values: Five<number> = [0, 0, 0, 0, 0];
  present: Five<boolean> = [false, false, false, false, false];
  cost: number | null = null;

  add(index: Index, value: number): void {
    this.values[index] = exactCount(this.values[index] + value);
    this.present[index] = true;
  }
  merge(row: Counts): void {
    for (const i of indices) if (row.present[i]) this.add(i, row.values[i]);
    if (row.cost !== null) this.cost = addCost(this.cost ?? 0, row.cost);
  }
  usage(source: TokenUsage['source'], cost = this.cost): TokenUsage {
    return {
      input_tokens: this.values[0], cached_input_tokens: this.values[1],
      cache_write_tokens: this.values[2], output_tokens: this.values[3], reasoning_output_tokens: this.values[4],
      input_tokens_present: this.present[0], cached_input_tokens_present: this.present[1],
      cache_write_tokens_present: this.present[2], output_tokens_present: this.present[3],
      reasoning_output_tokens_present: this.present[4], provider_cost_usd: cost, source,
      observed: this.present.some(Boolean), complete: this.present[0] && this.present[3],
    };
  }
}

function readTokens(sources: JsonObject[]): Counts {
  const row = new Counts();
  for (const i of indices) {
    for (const source of sources) {
      const name = aliases[i].find(name => Object.hasOwn(source, name));
      if (name === undefined) continue;
      row.add(i, tokenCount(source[name]));
      break;
    }
  }
  return row;
}

function providerCost(sources: JsonObject[]): number | null {
  for (const source of sources) {
    for (const name of ['total_cost_usd', 'cost_usd', 'totalCostUsd', 'costUSD']) {
      const cost = nonnegativeFloat(source[name]);
      if (cost !== null) return cost;
    }
  }
  return null;
}

/** Pi/OpenCode report fresh input separately from cache reads and writes. */
function splitInputRow(tokens: JsonObject, cache: JsonObject, read: string, write: string): Counts {
  const row = new Counts();
  if (Object.hasOwn(tokens, 'input') || Object.hasOwn(cache, read) || Object.hasOwn(cache, write)) {
    row.add(0, exactCount(tokenCount(tokens.input) + tokenCount(cache[read]) + tokenCount(cache[write])));
  }
  if (Object.hasOwn(cache, read)) row.add(1, tokenCount(cache[read]));
  if (Object.hasOwn(cache, write)) row.add(2, tokenCount(cache[write]));
  if (Object.hasOwn(tokens, 'output')) row.add(3, tokenCount(tokens.output));
  if (Object.hasOwn(tokens, 'reasoning')) row.add(4, tokenCount(tokens.reasoning));
  return row;
}

function unitTuple(usage: TokenUsage, turns: number): boolean {
  return usage.input_tokens === turns && usage.output_tokens === turns
    && usage.cached_input_tokens === 0 && usage.cache_write_tokens === 0 && usage.reasoning_output_tokens === 0;
}

/** Constant-memory port of Python extract_token_usage, including source precedence.
 * An invalid numeric range latches an error; later rows cannot hide lost usage.
 */
export class TokenUsageAccumulator {
  private cumulative: TokenUsage | null = null;
  private cost: number | null = null;
  private anthropic = new Counts();
  private anthropicRows = 0;
  private unitRows = 0;
  private resultTurns = 0;
  private delta = new Counts();
  private pi = new Counts();
  private opencode = new Counts();
  private failure: UsageAccountingError | null = null;

  consume(event: unknown): void {
    if (this.failure) throw this.failure;
    try { this.consumeEvent(event); } catch (error) {
      if (error instanceof UsageAccountingError) this.failure = error;
      throw error;
    }
  }

  private consumeEvent(event: unknown): void {
    if (!isJsonObject(event)) return;
    const data = object(event.data);
    const sources = [object(event.usage), object(data.usage), event, object(event.content)];
    const standard = readTokens(sources);
    this.cost = providerCost(sources) ?? this.cost;
    if (standard.present.some(Boolean)) this.cumulative = standard.usage('cumulative', this.cost);

    const type = text(event.type);
    const message = object(event.message);
    const usage = object(message.usage);
    if (type === 'assistant' && Object.keys(usage).length) {
      const row = readTokens([usage]);
      if (row.present.some(Boolean)) {
        this.anthropicRows = exactCount(this.anthropicRows + 1);
        if (row.present[0] && row.present[3] && row.values[0] <= 1 && row.values[3] <= 1
          && !row.values[1] && !row.values[2] && !row.values[4]) this.unitRows += 1;
        this.anthropic.merge(row);
      }
    }

    if (type === 'message_end' && text(message.role) === 'assistant' && Object.keys(usage).length) {
      const cost = nonnegativeFloat(object(usage.cost).total);
      const failed = ['error', 'aborted'].includes(text(message.stopReason)) || Boolean(text(message.errorMessage));
      const emptyError = failed && !['input', 'output', 'cacheRead', 'cacheWrite', 'reasoning']
        .some(name => tokenCount(usage[name]) !== 0) && !cost;
      if (emptyError) return;
      const row = splitInputRow(usage, usage, 'cacheRead', 'cacheWrite');
      row.cost = cost;
      this.pi.merge(row);
    }

    if (type === 'result') this.resultTurns = Math.max(this.resultTurns,
      tokenCount(truthy(event.num_turns) ? event.num_turns : event.numTurns));
    const camelNames: Five<string> = ['inputTokens', 'cachedInputTokens', 'cacheWriteTokens', 'outputTokens', 'reasoningOutputTokens'];
    for (const i of indices) {
      const name = camelNames[i];
      if (Object.hasOwn(data, name)) this.delta.add(i, tokenCount(data[name]));
    }

    const part = object(event.part);
    const tokens = object(part.tokens);
    if (Object.keys(tokens).length) {
      const row = splitInputRow(tokens, object(tokens.cache), 'read', 'write');
      row.cost = nonnegativeFloat(part.cost);
      this.opencode.merge(row);
    }
  }

  snapshot(): TokenUsage {
    if (this.failure) throw this.failure;
    const anthropic = this.anthropic.usage('per_message', this.cost);
    const cumulative = this.cumulative;
    if (this.anthropicRows && this.unitRows === this.anthropicRows
      && (this.resultTurns === 0 || this.resultTurns === this.anthropicRows)
      && (cumulative === null || unitTuple(cumulative, this.anthropicRows))) {
      return new Counts().usage('provider_request_units', this.cost);
    }
    if (cumulative !== null && this.resultTurns > 0 && unitTuple(cumulative, this.resultTurns)
      && anthropic.observed && (anthropic.input_tokens !== cumulative.input_tokens
        || anthropic.cached_input_tokens !== cumulative.cached_input_tokens
        || anthropic.output_tokens !== cumulative.output_tokens
        || anthropic.reasoning_output_tokens !== cumulative.reasoning_output_tokens)) return anthropic;
    if (cumulative !== null) return { ...cumulative, provider_cost_usd: cumulative.provider_cost_usd ?? this.cost };
    if (anthropic.observed) return anthropic;
    if (this.delta.present.some(Boolean)) return this.delta.usage('per_event');
    if (this.pi.present.some(Boolean)) return this.pi.usage('pi_message');
    if (this.opencode.present.some(Boolean)) return this.opencode.usage('per_step');
    return new Counts().usage('missing');
  }
}

export function extractTokenUsage(events: Iterable<unknown> | null | undefined): TokenUsage {
  const accumulator = new TokenUsageAccumulator();
  for (const event of events ?? []) accumulator.consume(event);
  return accumulator.snapshot();
}
