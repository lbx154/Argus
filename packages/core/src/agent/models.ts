// Ask each installed CLI for its own catalog. Cache per working directory and CLI,
// not per backend globally: project extensions can register entirely different models.
import { mkdtempSync, rmSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { isJsonObject } from '@argus/contracts';
import type { Backend, OfferedModel, OfferedModels } from '../types.js';
import { agentEnv, launcher, notInstalled } from './sandbox.js';
import { Cli } from './session.js';
import { dshOverlay } from './dsh.js';
import { priceBook, safeRates } from './prices.js';

export type Offered = OfferedModel;

/** Explicit whitelist: full Pi model definitions may also carry private HTTP headers. */
export function piModels(rows: unknown, selected?: unknown): Offered[] {
  const current = isJsonObject(selected) ? `${selected.provider}/${selected.id}` : '';
  return (Array.isArray(rows) ? rows.filter(isJsonObject) : []).flatMap(row => {
    if (typeof row.id !== 'string' || typeof row.provider !== 'string' || !row.id || !row.provider) return [];
    const model = `${row.provider}/${row.id}`;
    const cost = isJsonObject(row.cost) ? row.cost : {};
    const price = safeRates({ input: cost.input, output: cost.output, cachedInput: cost.cacheRead, cacheWrite: cost.cacheWrite });
    return [{ model, default: current === model, provider: row.provider, name: typeof row.name === 'string' ? row.name : row.id,
      reasoning: row.reasoning === true, available: true,
      ...price ? { price } : {} }];
  });
}

const run = promisify(execFile);
export const CATALOG_KIND: Record<Backend, 'live' | 'aliases' | 'manual'> = {
  codex: 'live', claude: 'live', copilot: 'live', cursor: 'live', pi: 'live', opencode: 'live', grok: 'manual', qoder: 'live', dsh: 'live',
};

/** JSON catalogs and unambiguous one-ID-per-line catalogs only, never arbitrary help prose. */
export function parseNativeCatalog(stdout: string, qualified = false): Offered[] {
  const plain = stdout.replace(/\u001b\[[0-9;]*m/g, '').trim();
  let value: unknown;
  try { value = JSON.parse(plain); } catch { value = plain.split(/\r?\n/).map(line => /^\s*(?:[-*]\s+)?([\w][\w.:/@+[\]-]*)\s*$/.exec(line)?.[1]).filter(Boolean); }
  const rows = Array.isArray(value) ? value : isJsonObject(value) && Array.isArray(value.models) ? value.models : [];
  return rows.flatMap(row => {
    const model = typeof row === 'string' ? row : isJsonObject(row) ? row.id ?? row.model ?? row.modelId : null;
    if (typeof model !== 'string' || !/^[\w][\w.:/@+[\]-]*$/.test(model) || /:\/\//.test(model)
      || /^(models?|available|provider|name|id|default|unauthorized)$/i.test(model) || qualified && !model.includes('/')) return [];
    return [{ model, default: isJsonObject(row) && (row.default === true || row.isDefault === true),
      ...isJsonObject(row) && typeof row.name === 'string' ? { name: row.name } : {} }];
  });
}
async function commandModels(backend: Backend, cwd: string, args: string[], qualified = false): Promise<Offered[]> {
  const [file, ...prefix] = launcher(backend);
  const { stdout } = await run(file!, [...prefix, ...args], { cwd, env: agentEnv(backend), timeout: 30_000, windowsHide: true, maxBuffer: 2 * 1024 * 1024 });
  return parseNativeCatalog(stdout, qualified);
}

export function claudeModels(data: unknown): Offered[] {
  const rows = isJsonObject(data) && Array.isArray(data.models) ? data.models.filter(isJsonObject) : [];
  // Initialization also includes account data. Only these model fields may cross the API boundary.
  return rows.flatMap(row => typeof row.value !== 'string' ? [] : [{
    model: row.value, default: row.value === 'default',
    ...typeof row.displayName === 'string' ? { name: row.displayName } : {},
    ...Array.isArray(row.supportedEffortLevels) ? { efforts: row.supportedEffortLevels.filter((value): value is string => typeof value === 'string') } : {},
  }]);
}

const ASK: Record<Backend, (cwd: string) => Promise<OfferedModels>> = {
  async codex(cwd) {
    const cli = new Cli([...launcher('codex'), 'app-server'], cwd, agentEnv('codex'));
    try {
      await cli.request('initialize', { clientInfo: { name: 'argus', title: 'Argus', version: '1' }, capabilities: null });
      const { data } = await cli.request('model/list', {});
      return (Array.isArray(data) ? data.filter(isJsonObject) : []).map(row => ({ model: String(row.model), default: row.isDefault === true,
        ...Array.isArray(row.supportedReasoningEfforts) ? { efforts: row.supportedReasoningEfforts.filter(isJsonObject).map(e => String(e.reasoningEffort)) } : {} }));
    } finally { await cli.close(); }
  },
  async copilot(cwd) {
    const cli = new Cli([...launcher('copilot'), '--acp'], cwd, agentEnv('copilot'), { jsonrpc: '2.0' });
    try {
      await cli.request('initialize', { protocolVersion: 1, clientCapabilities: {} });
      const { models } = await cli.request('session/new', { cwd, mcpServers: [] });
      const offered = isJsonObject(models) && Array.isArray(models.availableModels) ? models.availableModels.filter(isJsonObject) : [];
      const current = isJsonObject(models) ? models.currentModelId : undefined;
      const choices = offered.map(row => ({ model: String(row.modelId), default: row.modelId === current }));
      if (typeof current === 'string' && !choices.some(row => row.model === current)) choices.push({ model: current, default: true });
      return [...new Map(choices.map(row => [row.model, row])).values()];
    } finally { await cli.close(); }
  },
  async claude(cwd) {
    const cli = new Cli([...launcher('claude'), '-p', '--verbose', '--input-format', 'stream-json', '--output-format', 'stream-json', '--tools', ''], cwd, agentEnv('claude'));
    try { return claudeModels(await cli.claudeRequest('initialize')); }
    finally { await cli.close(); }
  },
  opencode: cwd => commandModels('opencode', cwd, ['models'], true),
  qoder: cwd => commandModels('qoder', cwd, ['--list-models']),
  async cursor(cwd) {
    const [file, ...prefix] = launcher('cursor');
    const { stdout } = await run(file!, [...prefix, '--help'], { cwd, env: agentEnv('cursor'), timeout: 10_000, windowsHide: true });
    if (!stdout.includes('--list-models')) return []; // older Cursor versions require a manual ID
    return commandModels('cursor', cwd, ['--list-models']);
  },
  grok: async () => [], // no verified model-list command; expose manual IDs, not a fabricated catalog
  async pi(cwd) {
    const cli = new Cli([...launcher('pi'), '--mode', 'rpc', '--no-session', '--no-tools', '--no-mcp'], cwd, agentEnv('pi'));
    try {
      const [catalog, state] = await Promise.all([cli.piRequest('get_available_models'), cli.piRequest('get_state')]);
      const rows = piModels(catalog.models, state.model);
      // thinkingLevelMap is a partial translation, not the supported-level list.
      // Ask Pi itself, inside this disposable in-memory session (never save defaults).
      for (const row of rows) {
        try {
          await cli.piRequest('set_model', { provider: row.provider, modelId: row.model.slice(row.provider!.length + 1) });
          const { levels } = await cli.piRequest('get_available_thinking_levels');
          if (Array.isArray(levels)) row.efforts = levels.filter((level): level is string => typeof level === 'string');
        } catch {
          // Some provider catalogs advertise retired models. One failed selection must not
          // hide the rest, nor masquerade as an available route.
          row.available = false;
          row.efforts = [];
          if (cli.gone) throw new Error('Pi exited while reading model capabilities. Refresh the catalog.');
        }
      }
      return rows;
    } finally { await cli.close(); }
  },
  async dsh(cwd) {
    const root = mkdtempSync(join(tmpdir(), 'argus-dsh-catalog-'));
    let cli: Cli | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const patch = dshOverlay(join(root, 'catalog'), { catalog: true, readOnly: true });
      cli = new Cli([...launcher('dsh'), '--profile', 'headless', '--patch', patch], cwd, agentEnv('dsh'));
      let offered: OfferedModels | undefined;
      let error = '';
      let phase = 'starting-cli';
      const process = cli;
      await Promise.race([cli.until(event => {
        if (event.type === 'argus_catalog_progress' && ['module-loaded', 'services-ready', 'profile-ready', 'models-listed'].includes(String(event.phase))) phase = String(event.phase);
        if (event.type === 'argus_catalog' && Array.isArray(event.models)) {
          offered = event.models.filter(isJsonObject).filter(row => typeof row.model === 'string').map(row => ({
            model: String(row.model), default: row.default === true,
            ...typeof row.name === 'string' ? { name: row.name } : {},
            ...typeof row.provider === 'string' ? { provider: row.provider } : {},
            ...Array.isArray(row.efforts) ? { efforts: row.efforts.filter((v): v is string => typeof v === 'string') } : {},
          }));
          if (Array.isArray(event.failedProviders) && event.failedProviders.length) offered.warning = 'Some DSH provider catalogs could not be loaded. Check DSH sign-in and refresh; the displayed list may be incomplete.';
        }
        if (event.type === 'error') error = String(event.message ?? 'DSH catalog failed.');
        return offered !== undefined || !!error;
      }), new Promise<never>((_, reject) => {
        timer = setTimeout(() => { void process.close(); reject(new Error(`DSH model discovery timed out after 30 seconds (${phase}).`)); }, 30_000);
      })]);
      if (!offered) throw new Error(error || cli.why());
      return offered;
    } finally { clearTimeout(timer); await cli?.close(); rmSync(root, { recursive: true, force: true }); }
  },
};

const asked = new Map<string, { at: number; result: Promise<OfferedModels> }>();
const TTL = 5 * 60_000;
export function models(backend: Backend, cwd: string, refresh = false): Promise<OfferedModels> {
  const key = JSON.stringify([backend, cwd, launcher(backend), process.env.PI_CODING_AGENT_DIR, process.env.DSH_HOME]);
  const cached = asked.get(key);
  if (!refresh && cached && Date.now() - cached.at < TTL) return cached.result;
  const missing = notInstalled(backend);
  if (missing) return Promise.reject(new Error(missing));
  const result = ASK[backend](cwd).then(rows => {
    for (const row of rows) priceBook.remember(backend, cwd, row.model, row.price);
    return Object.assign([...new Map(rows.map(row => [row.model, row])).values()], rows.warning ? { warning: rows.warning } : {});
  }).catch(error => {
    if (asked.get(key)?.result === result) asked.delete(key);
    throw error;
  });
  if (asked.size >= 64) asked.delete(asked.keys().next().value!);
  asked.set(key, { at: Date.now(), result });
  return result;
}
