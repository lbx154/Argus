// One-shot transports carried forward from 0.1.10. Each CLI keeps its own flag dialect,
// model ID and session ID. These are NOT aliases for Pi/DSH or for Claude's persistent RPC.
import { writeFileSync } from 'node:fs';
import { isJsonObject, type PayloadSchema } from '@argus/contracts';
import { count, type Usage } from '../types.js';
import type { StartBackend } from './call.js';
import { said, thought, used } from './steps.js';
import { MessageStream } from './streaming.js';

export type HeadlessBackend = 'cursor' | 'opencode' | 'grok' | 'qoder';
export const formPrompt = (prompt: string, schema: PayloadSchema): string =>
  `${prompt}\n\nYour final answer MUST be a single JSON object matching this JSON Schema, with no surrounding prose. This is the hand-off to Argus:\n${JSON.stringify(schema)}`;
export function parseForm(text: string): unknown {
  const body = text.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/, '$1');
  try { return JSON.parse(body); } catch { return undefined; }
}

/** Deny-by-default read-only profile, scoped to this child, not saved globally. */
export function openCodeEnvironment(readOnly: boolean): Record<string, string> {
  let config: Record<string, unknown> = {};
  const raw = process.env.OPENCODE_CONFIG_CONTENT;
  if (raw) {
    try { const value: unknown = JSON.parse(raw); if (!isJsonObject(value)) throw new Error(); config = value; }
    catch { throw new Error('OPENCODE_CONFIG_CONTENT must be a JSON object.'); }
  }
  const agent = config.agent;
  if (agent !== undefined && !isJsonObject(agent)) throw new Error('OpenCode agent configuration must be an object.');
  return { OPENCODE_CONFIG_CONTENT: JSON.stringify({ ...config, agent: { ...isJsonObject(agent) ? agent : {},
    'argus-role': { description: 'Argus role-bound tools', mode: 'primary', permission: {
      '*': 'deny', read: 'allow', glob: 'allow', grep: 'allow', ...readOnly ? {} : { edit: 'allow', bash: 'allow' },
    } },
  } }) };
}

const start = (backend: HeadlessBackend): StartBackend => (request, { schema, stem }) => {
  const readOnly = request.access === 'read';
  const dirs = [...request.readDirs ?? [], ...request.writeDirs ?? []].flatMap(dir => ['--add-dir', dir]);
  let args: string[];
  if (backend === 'cursor') {
    const model = request.model && request.effort && !request.model.includes('[') ? `${request.model}[effort=${request.effort}]` : request.model;
    args = ['-p', '--output-format', 'stream-json', '--workspace', request.cwd,
      ...readOnly ? ['--mode', 'ask'] : ['--force'], ...model ? ['--model', model] : [], ...dirs];
  } else if (backend === 'qoder') {
    args = ['-p', '--output-format', 'stream-json', ...readOnly ? ['--tools', 'Read,Glob,Grep'] : ['--permission-mode', 'accept_edits'],
      ...request.model ? ['--model', request.model] : [], ...request.effort ? ['--reasoning-effort', request.effort] : [], ...dirs];
  } else if (backend === 'opencode') {
    args = ['run', '--format', 'json', '--dir', request.cwd, '--agent', 'argus-role',
      ...request.model ? ['--model', request.model] : [], ...request.effort ? ['--variant', request.effort] : []];
  } else {
    const file = `${stem}.grok-prompt.txt`;
    const initial = request.instructions && !request.resume ? `${request.instructions}\n\n${request.prompt}` : request.prompt;
    writeFileSync(file, formPrompt(initial, schema), { mode: 0o600 });
    args = ['--no-auto-update', '--output-format', 'streaming-messages-json', '--verbatim', '--cwd', request.cwd,
      ...readOnly ? ['--tools', 'read_file,grep,list_dir'] : ['--yolo'],
      ...request.model ? ['--model', request.model] : [], ...request.effort ? ['--reasoning-effort', request.effort] : [], '--prompt-file', file];
  }
  if (request.resume) args.push(backend === 'opencode' ? '--session' : '--resume', request.resume);
  let threadId = request.resume ?? null;
  let model = request.model ?? null;
  let text = '', stepText = '';
  let payload: unknown;
  let completed = false, terminal = false;
  let error: string | null = null;
  let usage: Usage | null = null;
  return {
    args, formatPrompt: prompt => formPrompt(prompt, schema),
    ...backend === 'opencode' ? { env: openCodeEnvironment(readOnly) } : {},
    async turn(cli, prompt, { onStep, request: now }) {
      const stream = new MessageStream(now.onWords);
      await cli.once(backend === 'grok' ? '' : prompt, event => {
        if (event.agentId || event.parent_tool_use_id) return false;
        stream.claude(event);
        if (event.type === 'assistant') stream.clear();
        const session = event.sessionID ?? event.session_id;
        if (typeof session === 'string' && session) threadId = session;
        if (typeof event.model === 'string') model = event.model;
        if (event.type === 'error') {
          const details = isJsonObject(event.error) ? event.error : {};
          const data = isJsonObject(details.data) ? details.data : {};
          error = String(data.message ?? details.message ?? event.message ?? `${backend} failed.`);
        }
        if (backend === 'opencode') {
          const part = isJsonObject(event.part) ? event.part : {};
          if (event.type === 'text' && typeof part.text === 'string') {
            stepText += part.text; stream.text(0, part.text);
            const step = said(part.text); if (step) { stream.clear(); onStep(step); }
          }
          if (event.type === 'reasoning') onStep(thought([part.text]));
          if (event.type === 'tool_use' && isJsonObject(part.state)) onStep(used(part.tool, part.state.input));
          if (event.type === 'step_finish') {
            text = stepText; stepText = ''; stream.clear();
            const tokens = isJsonObject(part.tokens) ? part.tokens : null;
            const cache = tokens && isJsonObject(tokens.cache) ? tokens.cache : {};
            if (tokens) {
              usage ??= { input_tokens: 0, cached_input_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0, cost_usd: 0 };
              usage.input_tokens += count(tokens.input) + count(cache.read) + count(cache.write);
              usage.cached_input_tokens += count(cache.read); usage.output_tokens += count(tokens.output); usage.reasoning_output_tokens += count(tokens.reasoning);
              usage.cost_usd = typeof part.cost === 'number' && usage.cost_usd !== null ? usage.cost_usd + part.cost : null;
            }
            if (part.reason === 'stop') { completed = true; terminal = true; }
            else if (part.reason !== 'tool-calls' && part.reason !== 'tool_calls') { terminal = true; error = `OpenCode stopped with ${String(part.reason ?? 'unknown')}.`; }
          }
        } else {
          const message = isJsonObject(event.message) ? event.message : {};
          if (typeof message.model === 'string') model = message.model;
          if (event.type === 'assistant') {
            const content = Array.isArray(message.content) ? message.content.filter(isJsonObject) : [];
            const words = content.filter(part => part.type === 'text').map(part => String(part.text ?? '')).join('');
            if (words) text = words;
            for (const part of content) onStep(part.type === 'text' ? said(part.text) : part.type === 'thinking' ? thought([part.thinking]) : part.type === 'tool_use' ? used(part.name, part.input) : null);
          }
          if (event.type === 'result') {
            terminal = true;
            completed = event.subtype === 'success' && event.is_error !== true;
            if (backend === 'grok' && completed && event.stop_reason !== 'end_turn') { completed = false; error = `Grok Build stopped with ${String(event.stop_reason ?? 'unknown')}.`; }
            if (typeof event.result === 'string') text = event.result;
            payload = event.structured_output;
            if (!completed) error ??= text || `${backend} reported ${String(event.subtype ?? 'an error')}.`;
            const row = isJsonObject(event.usage) ? event.usage : null;
            if (row) usage = { input_tokens: count(row.input_tokens) + count(row.cache_read_input_tokens) + count(row.cache_creation_input_tokens),
              cached_input_tokens: count(row.cache_read_input_tokens), output_tokens: count(row.output_tokens),
              reasoning_output_tokens: count(isJsonObject(row.output_tokens_details) ? row.output_tokens_details.thinking_tokens : row.reasoning_tokens),
              cost_usd: typeof event.total_cost_usd === 'number' ? event.total_cost_usd : null };
          }
        }
        return false;
      });
      await cli.ended;
      const ok = terminal && completed && !error && cli.gone === 'exited with 0';
      return { threadId, completed: ok, model, usage, output: payload ?? parseForm(text), error: ok ? null : error ?? cli.why() };
    },
  };
};
export const cursor = start('cursor');
export const opencode = start('opencode');
export const grok = start('grok');
export const qoder = start('qoder');
