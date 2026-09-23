/** Provider transport receipts, distinct from Argus mission/domain events. */
import type { RunnerAccounting } from './accounting.js';

export type JsonObject = { [key: string]: unknown };

export type RunnerStopKind = 'cancelled' | 'wall_timeout' | 'idle_timeout' | 'output_limit' | 'transport_error' | 'provider_turn_limit';

export interface RunnerResult {
  command: string[];
  exitCode: number | null;
  signal: string | null;
  threadId: string | null;
  agentMessages: string[];
  turnCompleted: boolean;
  turnFailed: boolean;
  fatalError: string | null;
  stopKind: RunnerStopKind | null;
  stdoutLineCount: number;
  stderrLineCount: number;
  jsonEventCount: number;
  providerTurns: number;
  providerTurnCapHit: boolean;
  toolActivityObserved: boolean;
  accounting: RunnerAccounting;
}

export type RunnerStreamEvent =
  | { type: 'line'; stream: 'stdout' | 'stderr'; line: string }
  | { type: 'provider_event'; event: JsonObject }
  | { type: 'result'; result: RunnerResult };
