// `claude -p --output-format stream-json`: the prompt on stdin, the hand-off tool over HTTP
// MCP from a 0600 config file, usage and cost from the result event.

import { writeFileSync } from 'node:fs';
import { isJsonObject } from '@argus/contracts';
import type { Usage } from '../types.js';
import type { StartBackend } from './call.js';
import { SERVER, TOOL } from './handoff.js';

const count = (value: unknown): number => typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.trunc(value) : 0;

export const claude: StartBackend = (request, endpoint, { confined, secret }) => {
  writeFileSync(secret, JSON.stringify({
    mcpServers: { [SERVER]: { type: 'http', url: endpoint.url, headers: { Authorization: `Bearer ${endpoint.token}` } } },
  }), { mode: 0o600, flag: 'wx' });
  const args = [
    '-p', '--verbose', '--output-format', 'stream-json',
    ...request.model ? ['--model', request.model] : [],
    ...request.effort ? ['--effort', request.effort] : [],
    // Inside bubblewrap the sandbox is bubblewrap's; outside it, edits only.
    ...request.access === 'none' ? ['--tools', ''] : request.access === 'read' ? ['--tools', 'Read,Glob,Grep']
      : ['--permission-mode', confined ? 'bypassPermissions' : 'acceptEdits'],
    ...[...request.readDirs ?? [], ...request.writeDirs ?? []].flatMap(dir => ['--add-dir', dir]),
    // No permission mode pre-approves an MCP tool.
    '--mcp-config', secret, '--allowedTools', `mcp__${SERVER}__${TOOL}`,
    ...request.resume ? ['--resume', request.resume] : [],
  ];
  let threadId = request.resume ?? null;
  let model: string | null = null;
  let text = '';
  let result: Record<string, unknown> | null = null;
  return {
    args,
    consume(event) {
      if (typeof event.session_id === 'string' && event.session_id) threadId = event.session_id;
      if (event.type === 'system' && event.subtype === 'init' && typeof event.model === 'string') model = event.model;
      if (event.type === 'assistant' && isJsonObject(event.message) && Array.isArray(event.message.content)) {
        const said = event.message.content.filter(block => isJsonObject(block) && block.type === 'text').map(block => String(block.text)).join('\n');
        if (said.trim()) text = said;
      }
      if (event.type === 'result') result = event;
    },
    finish() {
      const completed = result?.subtype === 'success' && !result.is_error;
      const said = typeof result?.result === 'string' ? result.result.trim() : '';
      const reported = isJsonObject(result?.usage) ? result.usage : null;
      // Anthropic counts input apart from cache reads and writes; Argus's input includes both.
      const usage: Usage | null = reported && {
        input_tokens: count(reported.input_tokens) + count(reported.cache_read_input_tokens) + count(reported.cache_creation_input_tokens),
        cached_input_tokens: count(reported.cache_read_input_tokens),
        output_tokens: count(reported.output_tokens),
        reasoning_output_tokens: count(isJsonObject(reported.output_tokens_details) ? reported.output_tokens_details.thinking_tokens : 0),
        cost_usd: typeof result?.total_cost_usd === 'number' ? result.total_cost_usd : null,
      };
      return {
        threadId, completed, model: model ?? request.model ?? null, usage,
        error: completed ? null : result ? said || `Claude reported ${String(result.subtype ?? 'an error')}.` : null,
        text: completed && said ? said : text,
      };
    },
  };
};
