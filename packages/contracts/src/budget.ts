import definition from '../schemas/budget_bridge_protocol.json' with { type: 'json' };
import type { RunnerResult, RunnerStreamEvent } from './runner.js';

export const BUDGET_BRIDGE = definition;
export interface BudgetedResult {
  callId: string | null;
  admitted: boolean;
  runner: RunnerResult | null;
  settlement: 'not_started' | 'settled' | 'unresolved' | 'failed';
  reason: string;
}
export type BudgetedStreamEvent = Exclude<RunnerStreamEvent, { type: 'result' }>
  | { type: 'result'; result: BudgetedResult };
