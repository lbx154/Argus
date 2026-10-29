// The models a backend offers, asked of the backend itself: Codex's app-server lists its catalog
// (`model/list`), Copilot names its models when an ACP session opens, and Claude Code takes the
// aliases of its current models. Pi is asked nothing yet: any model is taken by name. Asked once
// a process, and again after a failure.

import { isJsonObject } from '@argus/contracts';
import type { Backend } from '../types.js';
import { agentEnv, launcher } from './sandbox.js';
import { Cli } from './session.js';

/** A model the backend offers: its name, as the CLI takes it, and whether it is the backend's default. */
export interface Offered { model: string; default: boolean }

const ASK: Record<Backend, (cwd: string) => Promise<Offered[]>> = {
  async codex(cwd) {
    const cli = new Cli([...launcher('codex'), 'app-server'], cwd, agentEnv('codex'));
    try {
      await cli.request('initialize', { clientInfo: { name: 'argus', title: 'Argus', version: '1' }, capabilities: null });
      const { data } = await cli.request('model/list', {});
      return (Array.isArray(data) ? data.filter(isJsonObject) : []).map(row => ({ model: String(row.model), default: row.isDefault === true }));
    } finally { void cli.close(); }
  },
  async copilot(cwd) {
    const cli = new Cli([...launcher('copilot'), '--acp'], cwd, agentEnv('copilot'), { jsonrpc: '2.0' });
    try {
      await cli.request('initialize', { protocolVersion: 1, clientCapabilities: {} });
      const { models } = await cli.request('session/new', { cwd, mcpServers: [] });
      const offered = isJsonObject(models) && Array.isArray(models.availableModels) ? models.availableModels.filter(isJsonObject) : [];
      const current = isJsonObject(models) ? models.currentModelId : undefined;
      return [...new Map(offered.map(row => [String(row.modelId), { model: String(row.modelId), default: row.modelId === current }])).values()];
    } finally { void cli.close(); }
  },
  claude: async () => ['opus', 'sonnet', 'haiku'].map(model => ({ model, default: false })),
  pi: async () => [],
};

const asked = new Map<Backend, Promise<Offered[]>>();

export function models(backend: Backend, cwd: string): Promise<Offered[]> {
  if (!asked.has(backend)) asked.set(backend, ASK[backend](cwd).catch(() => { asked.delete(backend); return []; }));
  return asked.get(backend)!;
}
