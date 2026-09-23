import { isJsonObject, type JsonObject, type RunnerResult, type RunnerStreamEvent } from '@argus/contracts';
import { PiEventConsumer } from './piEvents.js';
import { PiAccountingAccumulator } from './piAccounting.js';
import { executeProcess, type ProcessOptions, type ProcessGuardianOptions } from './process.js';
import { PI_OUTPUT_SCHEMA_EXTENSION, piChildEnvironment, snapshotPiRequest, validatePiCapabilities } from './piConfiguration.js';

export interface PiRunRequest {
  prompt: string;
  cwd: string;
  model?: string;
  provider?: string;
  reasoningEffort?: 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
  sessionDirectory?: string;
  resumeSession?: string;
  toolPolicy: 'disabled' | 'read-only' | 'workspace-write';
  skillPaths?: string[];
  outputSchema?: JsonObject;
  trustedExtensions?: string[];
  trustedToolNames?: string[];
  extensionEnv?: Record<string, string>;
  /** Observed assistant-turn allowance. Zero leaves the call uncapped. */
  providerTurnCap?: number;
  signal?: AbortSignal;
  wallTimeoutMs?: number;
  idleTimeoutMs?: number;
  maxRetainedTextBytes?: number;
}

export interface PiBackendOptions {
  executable?: string;
  /** Supports a direct `node /path/to/pi.js` launcher without a shell. */
  prefixArgs?: string[];
  env?: NodeJS.ProcessEnv;
  terminateGraceMs?: number;
  maxLineBytes?: number;
  maxBufferedBytes?: number;
  guardian?: ProcessGuardianOptions;
}

export interface RunnerBackend {
  fork(): RunnerBackend;
  run(request: PiRunRequest): AsyncIterable<RunnerStreamEvent>;
}

export function buildPiCommand(request: PiRunRequest): string[] {
  if (!request.prompt.trim()) throw new Error('prompt must not be empty');
  if (request.resumeSession && !request.sessionDirectory) throw new Error('sessionDirectory is required to resume a session');
  if (!['disabled', 'read-only', 'workspace-write'].includes(request.toolPolicy)) throw new Error('toolPolicy must be explicit');
  validatePiCapabilities(request);
  const args = ['--mode', 'json'];
  if (request.sessionDirectory) args.push('--session-dir', request.sessionDirectory);
  else args.push('--no-session');
  args.push('--no-extensions', '--no-skills', '--no-prompt-templates', '--no-themes', '--no-context-files', '--no-approve');
  for (const path of request.skillPaths ?? []) args.push('--skill', path);
  if (request.toolPolicy !== 'disabled') {
    for (const path of request.trustedExtensions ?? []) args.push('--extension', path);
  }
  if (request.outputSchema !== undefined) args.push('--extension', PI_OUTPUT_SCHEMA_EXTENSION);
  const model = request.model?.trim();
  const provider = request.provider?.trim();
  if (model) args.push('--model', provider && !model.includes('/') ? `${provider}/${model}` : model);
  if (request.reasoningEffort) args.push('--thinking', request.reasoningEffort);
  if (request.toolPolicy === 'disabled') args.push('--no-tools');
  else if (request.toolPolicy === 'read-only') args.push('--tools', [...new Set(['read', 'grep', 'find', 'ls', ...(request.trustedToolNames ?? [])])].join(','));
  if (request.resumeSession) args.push('--session', request.resumeSession);
  return args;
}

/** Experimental transport adapter; the Python daemon still owns task/budget state. */
export class PiBackend implements RunnerBackend {
  constructor(private readonly options: PiBackendOptions = {}) {}
  fork(): PiBackend { return new PiBackend({ ...this.options }); }

  async *run(request: PiRunRequest): AsyncGenerator<RunnerStreamEvent> {
    request = snapshotPiRequest(request);
    const args = [...(this.options.prefixArgs ?? []), ...buildPiCommand(request)];
    const executable = this.options.executable ?? 'pi';
    const limit = request.maxRetainedTextBytes ?? 1_048_576;
    if (!Number.isSafeInteger(limit) || limit <= 0) throw new Error('maxRetainedTextBytes must be a positive integer');
    const consumer = new PiEventConsumer();
    const accounting = new PiAccountingAccumulator(request.model?.trim(), request.provider?.trim());
    consumer.threadId = request.resumeSession ?? null;
    const controller = new AbortController();
    const signal = request.signal ? AbortSignal.any([request.signal, controller.signal]) : controller.signal;
    let stdoutLineCount = 0;
    let stderrLineCount = 0;
    let jsonEventCount = 0;
    let textLimit = false;
    let providerTurnCapHit = false;
    const processOptions: ProcessOptions = {
      ...this.options, env: piChildEnvironment(request, this.options.env), executable, args, cwd: request.cwd, input: request.prompt, signal,
      ...(request.wallTimeoutMs === undefined ? {} : { wallTimeoutMs: request.wallTimeoutMs }),
      ...(request.idleTimeoutMs === undefined ? {} : { idleTimeoutMs: request.idleTimeoutMs }),
    };
    for await (const item of executeProcess(processOptions)) {
      if (item.type === 'line') {
        if (item.stream === 'stdout') stdoutLineCount += 1;
        else stderrLineCount += 1;
        yield item;
        if (item.stream !== 'stdout' || textLimit) continue;
        let parsed: unknown;
        try { parsed = JSON.parse(item.line); } catch { continue; }
        if (!isJsonObject(parsed)) continue;
        jsonEventCount += 1;
        accounting.consume(parsed);
        consumer.consume(parsed);
        if (consumer.agentMessages.reduce((bytes, message) => bytes + Buffer.byteLength(message), 0) > limit) {
          textLimit = true;
          controller.abort();
          // Do not expose a truncated reply as a final assistant answer.
          consumer.agentMessages = [];
        }
        if (!signal.aborted && !consumer.turnFailed && (request.providerTurnCap ?? 0) > 0 && consumer.providerTurns >= request.providerTurnCap!) {
          providerTurnCapHit = true;
          controller.abort();
        }
        yield { type: 'provider_event', event: parsed };
      } else {
        // A completed process can have buffered output left to consume. A cap
        // which did not stop it must not invalidate that successful receipt.
        const capHit = providerTurnCapHit && item.stopKind === 'cancelled';
        const stopKind = textLimit ? 'output_limit' : capHit ? 'provider_turn_limit' : item.stopKind;
        const failed = consumer.turnFailed || stopKind !== null || item.code !== 0 || !consumer.turnCompleted;
        const result: RunnerResult = {
          command: [executable, ...args], exitCode: item.code, signal: item.signal,
          threadId: consumer.threadId, agentMessages: [...consumer.agentMessages],
          turnCompleted: consumer.turnCompleted && !failed, turnFailed: failed,
          fatalError: textLimit ? 'Retained assistant text exceeded its byte limit.'
            : capHit ? `Provider turn cap reached (${request.providerTurnCap}); the caller may continue from the retained work in a new call.`
            : item.error ?? consumer.fatalError ?? (failed ? `Pi exited ${item.code ?? item.signal ?? 'without an exit code'} without a successful settled turn.` : null),
          stopKind, stdoutLineCount, stderrLineCount, jsonEventCount,
          providerTurns: consumer.providerTurns, providerTurnCapHit: capHit, toolActivityObserved: consumer.toolActivityObserved,
          accounting: accounting.snapshot(consumer.turnCompleted && !failed),
        };
        yield { type: 'result', result };
      }
    }
  }
}
