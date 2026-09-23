import { isJsonObject, validProjectId, type BudgetedResult, type BudgetedStreamEvent, type RunnerResult } from '@argus/contracts';
import { PiBackend, buildPiCommand, type PiBackendOptions, type PiRunRequest } from './pi.js';
import { PiAccountingAccumulator } from './piAccounting.js';
import { PythonBudgetSession, type PythonBudgetOptions } from './budgetClient.js';

export interface BudgetedPiRequest extends PiRunRequest {
  projectId: string;
  model: string;
  provider: string;
  missionId?: string;
  runLabel?: string;
}
export interface BudgetedPiOptions {
  python: PythonBudgetOptions;
  pi?: PiBackendOptions;
}

/** Drain the provider independently of a slow UI, with a bounded event queue. */
class EventQueue {
  private readonly events: Array<{ event: BudgetedStreamEvent; size: number }> = [];
  private bytes = 0;
  private wake: (() => void) | undefined;
  private result: BudgetedResult | undefined;
  push(event: BudgetedStreamEvent): boolean {
    const size = Buffer.byteLength(JSON.stringify(event));
    if (this.events.length >= 1024 || this.bytes + size > 4_194_304) return false;
    this.events.push({ event, size }); this.bytes += size; this.wake?.(); return true;
  }
  finish(result: BudgetedResult): void { this.result = result; this.wake?.(); }
  async *read(): AsyncGenerator<BudgetedStreamEvent> {
    while (true) {
      const item = this.events.shift();
      if (item) { this.bytes -= item.size; yield item.event; }
      else if (this.result) { yield { type: 'result', result: this.result }; return; }
      else await new Promise<void>(resolve => { this.wake = resolve; });
    }
  }
}

/** Opt-in execution path: TS transport/accounting, Python budget and ledger owner. */
export class BudgetedPiBackend {
  constructor(private readonly options: BudgetedPiOptions) {}

  async *run(request: BudgetedPiRequest): AsyncGenerator<BudgetedStreamEvent> {
    buildPiCommand(request);
    if (!validProjectId(request.projectId) || !request.model.trim() || !request.provider.trim()) throw new Error('project, model and provider must be explicit');
    const controller = new AbortController();
    const signal = request.signal ? AbortSignal.any([request.signal, controller.signal]) : controller.signal;
    const queue = new EventQueue();
    const pump = this.execute(request, signal, controller, queue);
    try { yield* queue.read(); }
    finally { controller.abort(); await pump; }
  }

  private async execute(request: BudgetedPiRequest, signal: AbortSignal, controller: AbortController, queue: EventQueue): Promise<void> {
    let session: PythonBudgetSession | undefined;
    let admitted = false;
    let callId: string | null = null;
    let runner: RunnerResult | null = null;
    let settlement: BudgetedResult['settlement'] = 'not_started';
    let reason = '';
    let timer: NodeJS.Timeout | undefined;
    let observing: Promise<void> | null = null;
    let dirty = false;
    let budgetError: Error | null = null;
    let overflow = false;
    try {
      if (signal.aborted) { reason = 'cancelled before admission'; return; }
      session = new PythonBudgetSession(this.options.python);
      const ownerSignal = AbortSignal.any([signal, session.failure.signal]);
      const model = request.model.includes('/') ? request.model : `${request.provider}/${request.model}`;
      const admission = await session.request('reserve', { sid: request.projectId, model,
        mission_id: request.missionId ?? null, run_label: request.runLabel ?? 'typescript-pi' });
      if (typeof admission.admitted !== 'boolean' || typeof admission.call_id !== 'string'
        || typeof admission.reason !== 'string' || admission.source_root !== session.sourceRoot
        || admission.global_root !== session.globalRoot) throw new Error('incompatible budget owner identity');
      callId = admission.call_id; admitted = admission.admitted; reason = admission.reason;
      if (!admitted) return;
      if (ownerSignal.aborted) { await session.request('release'); reason = 'cancelled before start'; return; }
      const started = await session.request('start');
      if (started.started !== true) throw new Error('budget owner did not confirm the durable start receipt');
      settlement = 'unresolved';
      const accounting = new PiAccountingAccumulator(request.model, request.provider);
      const observe = (): Promise<void> => {
        dirty = true;
        observing ??= (async () => {
          while (dirty) {
            dirty = false;
            const reply = await session!.request('observe', { accounting: accounting.snapshot(false) });
            if (typeof reply.stop_reason !== 'string') throw new Error('invalid budget observation receipt');
            if (reply.stop_reason) { reason ||= reply.stop_reason; controller.abort(); }
          }
        })().catch(error => {
          budgetError = error instanceof Error ? error : new Error('budget observation failed');
          controller.abort();
        }).finally(() => { observing = null; });
        return observing;
      };
      await observe();
      if (budgetError) throw budgetError;
      timer = setInterval(() => { if (!observing && !budgetError) void observe(); }, 1000);
      for await (const event of new PiBackend({ ...this.options.pi, guardian: this.options.python }).run({ ...request, signal: ownerSignal })) {
        if (event.type === 'result') runner = event.result;
        else {
          if (event.type === 'provider_event') {
            accounting.consume(event.event);
            const diagnostic = accounting.snapshot(false).error;
            if (diagnostic) { reason ||= diagnostic; controller.abort(); }
            if (event.event.type === 'message_end') await observe();
          }
          if (!overflow && !queue.push(event)) {
            overflow = true; reason ||= 'budgeted runner output queue exceeded its limit'; controller.abort();
          }
        }
      }
      clearInterval(timer);
      if (observing) await observing;
      if (budgetError) throw budgetError;
      if (!runner) throw new Error('provider returned no terminal receipt');
      if (overflow) runner = { ...runner, turnCompleted: false, turnFailed: true, stopKind: 'output_limit', fatalError: reason };
      const receipt = await session.request('settle', { accounting: runner.accounting,
        completed: runner.turnCompleted && !reason, thread_id: runner.threadId });
      if (!isJsonObject(receipt) || receipt.call_id !== callId || !['settled', 'unresolved'].includes(String(receipt.settlement))) {
        throw new Error('invalid settlement receipt');
      }
      settlement = receipt.settlement as 'settled' | 'unresolved';
    } catch (error) {
      controller.abort();
      settlement = 'failed'; reason ||= error instanceof Error ? error.message : 'budgeted execution failed';
    } finally {
      clearInterval(timer);
      if (observing) await observing;
      try { if (session) await session.close(); }
      catch { settlement = 'failed'; reason ||= 'budget owner cleanup failed'; }
      finally { queue.finish({ callId, admitted, runner, settlement, reason }); }
    }
  }
}
