// DSH 0.2.1-rc.5+ headless JSON interface. The profile owns authentication;
// Argus passes only a selected route and the role's access policy in an overlay.
import { writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { isJsonObject } from '@argus/contracts';
import type { StartBackend } from './call.js';
import { count, type Usage } from '../types.js';
import { said, thought, used } from './steps.js';
import { formPrompt } from './headless.js';
import { MessageStream } from './streaming.js';

/** An additive per-process overlay, never editing the operator's DSH profile. */
export function dshOverlay(stem: string, options: { model?: string; effort?: string; readOnly?: boolean; catalog?: boolean } = {}): string {
  const patch = `${stem}.dsh.patch.yml`;
  const observer = `${stem}.dsh-observer.mjs`;
  // Read model metadata through DSH's own services; no profile/config/credential dumps.
  writeFileSync(observer, `export const name = 'argus-route-observer';
export const inject = ${JSON.stringify(options.catalog ? [] : ['llm', 'agentDefaultModel'])};
const progress = phase => { if (${Boolean(options.catalog)}) process.stdout.write(JSON.stringify({type:'argus_catalog_progress',phase}) + '\\n'); };
progress('module-loaded');
export function apply(ctx) {
  // Public DSH agent stream API. Export visible text only, with session identity;
  // the host drops subagent/auxiliary streams and still waits for final + exit.
  if (!${Boolean(options.catalog)}) ctx.on('agent/assistant-stream', ({ agent, frame }) => {
    if (frame.type === 'start') process.stdout.write(JSON.stringify({ type: 'argus_live', sessionId: agent.session.id, phase: 'start' }) + '\\n');
    if (frame.type === 'chunk' && frame.chunk.type === 'text-delta') process.stdout.write(JSON.stringify({ type: 'argus_live', sessionId: agent.session.id, phase: 'text', index: frame.chunk.index, text: frame.chunk.text }) + '\\n');
  });
  progress('services-ready');
  void (async () => {
    await ctx.get('loader')?.await();
    progress('profile-ready');
    const selected = ctx.get('agentDefaultModel').currentSelection();
    const selectedId = selected.provider + '/' + selected.model;
    if (${Boolean(options.catalog)}) {
      const llm = ctx.get('llm');
      const failures = [];
      const groups = await Promise.all(llm.listProviders().map(async provider => {
        try {
          return (await llm.listModels(provider.id)).map(model => ({
            model: provider.id + '/' + model.id, provider: provider.id, name: model.name || model.id,
            default: provider.id + '/' + model.id === selectedId
          }));
        } catch { failures.push(provider.id); return []; }
      }));
      progress('models-listed');
      const models = groups.flat();
      if (!models.some(m => m.model === selectedId)) models.push({ model: selectedId, default: true, provider: selected.provider, name: selected.model });
      // Exact-model capability lookup may involve provider I/O. It is optional and must
      // never hide the catalog or fan out one unbounded request per advertised model.
      let next = 0;
      const metadataDeadline = Date.now() + 2000;
      const metadata = async () => {
        while (next < models.length && Date.now() < metadataDeadline) {
          const row = models[next++];
          const remaining = Math.max(1, metadataDeadline - Date.now());
          let timer;
          try {
            const info = await Promise.race([
              llm.resolveModelInfo(row.provider, row.model.slice(row.provider.length + 1), AbortSignal.timeout(remaining)),
              new Promise(resolve => { timer = setTimeout(() => resolve(undefined), remaining); })
            ]);
            if (info?.reasoning?.efforts) row.efforts = info.reasoning.efforts.map(e => e.id);
          } catch { /* retain the advertised model, using the CLI's default thinking level */ }
          finally { clearTimeout(timer); }
        }
      };
      await Promise.all(Array.from({ length: Math.min(4, models.length) }, metadata));
      process.stdout.write(JSON.stringify({ type: 'argus_catalog', models, failedProviders: failures }) + '\\n');
      ctx.get('appExit')(0);
    } else {
      process.stdout.write(JSON.stringify({ type: 'argus_route', model: selectedId, effort: selected.reasoningEffort }) + '\\n');
    }
  })().catch(() => {
    process.stdout.write(JSON.stringify({ type: 'error', message: 'DSH model metadata could not be loaded. Check the headless profile in DSH.' }) + '\\n');
    ctx.get('appExit')(1);
  });
}
`);
  const entries: Record<string, unknown>[] = [
    { id: 'credentials', disabled: false }, { id: 'authorization', disabled: false }, { id: 'deepseek-account', disabled: false },
    { id: 'approval', config: { policy: 'never' } },
    { id: 'tool-bash', disabled: process.platform === 'win32' || !!options.readOnly },
    { id: 'tool-pwsh', disabled: process.platform !== 'win32' || !!options.readOnly },
    // A read-only parent cannot delegate writes or execute arbitrary subprocesses.
    ...options.readOnly ? ['tool-jobs', 'tool-subagent', 'tool-subagent-fork', 'tool-subagent-control', 'tool-workflow', 'tool-ralph']
      .map(id => ({ id, disabled: true })) : [],
    { insert: [{ id: 'argus-route-observer', name: pathToFileURL(observer).href }] },
  ];
  if (options.model) {
    const slash = options.model.indexOf('/');
    if (slash < 1 || slash === options.model.length - 1) throw new Error('DSH requires a provider/model ID.');
    entries.push({ id: 'agent-default-model', config: { provider: options.model.slice(0, slash), model: options.model.slice(slash + 1),
      ...options.effort ? { reasoningEffort: options.effort } : {} } });
  } else if (options.effort) entries.push({ id: 'agent-default-model', config: { reasoningEffort: options.effort } });
  // Keep the command-line startup service: disabling it can leave profile initialization
  // waiting forever. Disabling only the runner guarantees no Agent/inference is started.
  if (options.catalog) entries.push({ id: 'headless-runner', disabled: true }, { id: 'headless-startup', disabled: false });
  writeFileSync(patch, JSON.stringify(entries, null, 2));
  return patch;
}

/** Only an entire JSON object (optionally one fenced block) is a hand-off, never a prose substring. */
export function dshHandOff(text: string): unknown {
  const body = text.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/, '$1');
  try { return JSON.parse(body); } catch { return undefined; }
}

export const dsh: StartBackend = (request, { schema, stem }) => {
  const patch = dshOverlay(stem, { ...request.model ? { model: request.model } : {},
    ...request.effort ? { effort: request.effort } : {}, readOnly: request.access === 'read' });
  let threadId = request.resume ?? null;
  let model = request.model ?? null;
  let final: string | undefined;
  let completed = false;
  let error: string | null = null;
  let usage: Usage | null = null;
  return {
    args: ['--profile', 'headless', '--patch', patch, '--json', ...request.resume ? ['--session-id', request.resume] : []],
    env: { DSH_PERMISSION_MODE: request.access === 'read' ? 'read-only' : 'workspace-write' },
    formatPrompt: prompt => formPrompt(prompt, schema),
    async turn(cli, prompt, { onStep, request: now }) {
      const stream = new MessageStream(now.onWords);
      // DSH's headless transport has no JSON-schema flag. Require the same form in the final
      // answer and run the normal strict schema validator; prose is a failed hand-off, not success.
      await cli.once(prompt, event => {
        if (event.type === 'session' && typeof event.sessionId === 'string') threadId = event.sessionId;
        if (event.type === 'argus_route' && typeof event.model === 'string') model = event.model;
        if (event.type === 'argus_live' && threadId && event.sessionId === threadId) {
          if (event.phase === 'start') stream.clear();
          if (event.phase === 'text' && typeof event.text === 'string') stream.text(Number(event.index), event.text);
        }
        if (event.type === 'text') { stream.clear(); onStep(said(event.text)); }
        if (event.type === 'thinking') onStep(thought([event.text]));
        if (event.type === 'tool_call') onStep(used(event.tool, event.input));
        if (event.type === 'error') error = String(event.message ?? 'DSH failed.');
        if (event.type === 'status' && event.phase === 'turn_end') {
          const reason = isJsonObject(event.reason) ? event.reason : {};
          completed = reason.kind === 'completed';
          if (reason.kind === 'error') error = isJsonObject(reason.error) ? String(reason.error.message ?? reason.error.code) : 'DSH turn failed.';
        }
        if (event.type === 'status' && event.phase === 'step_end' && isJsonObject(event.usage)) {
          usage ??= { input_tokens: 0, cached_input_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0, cost_usd: null };
          // DSH's native counters are disjoint, unlike Argus's inclusive input.
          usage.input_tokens += count(event.usage.inputTokens) + count(event.usage.cacheReadTokens) + count(event.usage.cacheWriteTokens);
          usage.cached_input_tokens += count(event.usage.cacheReadTokens);
          usage.cache_write_tokens = (usage.cache_write_tokens ?? 0) + count(event.usage.cacheWriteTokens);
          usage.output_tokens += count(event.usage.outputTokens);
          usage.reasoning_output_tokens += count(event.usage.reasoningTokens);
        }
        if (event.type === 'final' && typeof event.text === 'string') final = event.text;
        // Wait for process exit too: DSH can emit final and then exit unsuccessfully.
        return false;
      });
      await cli.ended;
      const ok = completed && final !== undefined && !error && cli.gone === 'exited with 0';
      return { threadId, model, completed: ok, error: ok ? null : error ?? cli.why(), usage,
        output: final === undefined ? undefined : dshHandOff(final) };
    },
  };
};
