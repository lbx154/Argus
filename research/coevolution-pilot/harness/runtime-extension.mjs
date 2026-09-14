import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const learning = process.env.PILOT_LEARNING === '1';
const enabled = learning || ['pi_runtime', 'joint'].includes(process.env.PILOT_CONDITION);
const directory = '/learning/pi';
const active = path.join(directory, 'inspect_data.py');
const observations = new Set();
const workObservations = new Set();
const reference = value => String(value).split('|', 1)[0];
let acceptedThisRun = 0;
const base = ['read', 'bash', 'write', 'edit'];
const log = (kind, data) => fs.appendFileSync('/experiment/runtime-events.jsonl',
  JSON.stringify({ timestamp: new Date().toISOString(), kind, ...data }) + '\n');

export default function (pi) {
  const setTools = () => pi.setActiveTools([...base,
    ...(learning ? ['evolve_runtime'] : []), ...(enabled && fs.existsSync(active) ? ['inspect_data'] : [])]);
  pi.on('session_start', () => { setTools(); log('runtime_started', { condition: process.env.PILOT_CONDITION, learning, inspector: enabled && fs.existsSync(active) }); });
  pi.on('tool_call', (event) => {
    if (event.toolName === 'bash' && /(?:^|[\s;&|])(?:argus-pi|copilot|codex|claude|pi)(?:\s|$)/.test(event.input.command || '')) {
      return { block: true, reason: 'This bounded experiment does not allow spawning another model agent.' };
    }
  });
  pi.on('tool_result', (event) => {
    const text = (event.content || []).filter(item => item.type === 'text').map(item => item.text).join('\n');
    observations.add(reference(event.toolCallId));
    if (event.toolName !== 'evolve_runtime') workObservations.add(reference(event.toolCallId));
    log('tool_observation', { toolCallId: reference(event.toolCallId), tool: event.toolName, error: !!event.isError,
      input: event.toolName === 'evolve_runtime' ? { rationale: event.input.rationale } : event.input,
      text_characters: text.length, excerpt: text.slice(0, 900) });
    return { content: [...(event.content || []), { type: 'text', text: `[observation:${reference(event.toolCallId)}]` }] };
  });
  pi.registerTool({
    name: 'inspect_data', label: 'Learned data inspector',
    description: 'Use the runtime inspection helper learned during earlier work. Preview CSV, XLSX or PDF structure and a bounded sample; this does not solve the task or modify input.',
    parameters: { type: 'object', properties: { path: { type: 'string' }, limit: { type: 'integer', minimum: 1, maximum: 12 } }, required: ['path'], additionalProperties: false },
    async execute(id, input, signal) {
      try {
        const result = await exec('python', [active, input.path, '--limit', String(input.limit || 5)], { timeout: 30000, signal, maxBuffer: 150000 });
        const version = JSON.parse(fs.readFileSync(path.join(directory, 'active.json'), 'utf8')).version;
        log('evolved_tool_used', { toolCallId: reference(id), path: input.path, version, stdout_characters: result.stdout.length });
        return { content: [{ type: 'text', text: result.stdout }], details: { version } };
      } catch (error) {
        throw new Error(String(error.stderr || error.message).slice(0, 4000));
      }
    },
  });
  pi.registerTool({
    name: 'evolve_runtime', label: 'Evolve Pi inspection runtime',
    description: 'Propose a reusable Python inspection helper based on at least three completed tools and two cited observation IDs. The helper CLI is `python helper.py PATH --limit N`; stdout must be a bounded JSON object with kind and items, preserve source identifiers and zeros, handle CSV/XLSX/PDF, and fail visibly on missing files. Use csv/openpyxl/pdfplumber or pypdf. No task answers or task-specific paths. Transparent format/read-only contract checks run before activation; at most one accepted revision per task. After acceptance, inspect_data becomes available immediately in this same session.',
    parameters: { type: 'object', properties: { source: { type: 'string' }, rationale: { type: 'string' }, evidence: { type: 'array', items: { type: 'string' }, minItems: 2 } }, required: ['source', 'rationale', 'evidence'], additionalProperties: false },
    async execute(id, input, signal) {
      const unknown = input.evidence.map(reference).filter(item => !observations.has(item));
      if (!learning || acceptedThisRun || workObservations.size < 3 || unknown.length) {
        log('runtime_revision_not_admitted', { toolCallId: reference(id), observations: workObservations.size,
          already_accepted: acceptedThisRun, cited_evidence: input.evidence.map(reference) });
        throw new Error(`Not admitted: work observations=${workObservations.size}, already accepted=${acceptedThisRun}, unknown references=${JSON.stringify(unknown)}. Cite observation IDs from actual tool results; validation failures are also valid evidence.`);
      }
      fs.mkdirSync(directory, { recursive: true });
      fs.mkdirSync(path.join(directory, 'history'), { recursive: true });
      const candidate = path.join(directory, 'candidate.py');
      fs.writeFileSync(candidate, input.source);
      try {
        await exec('python', ['-m', 'py_compile', candidate], { timeout: 10000, signal });
        const check = await exec('python', ['/opt/pilot/contract_check.py', candidate], { timeout: 55000, signal, maxBuffer: 30000 });
        const prior = fs.existsSync(path.join(directory, 'active.json')) ? JSON.parse(fs.readFileSync(path.join(directory, 'active.json'), 'utf8')) : { version: 0 };
        const version = prior.version + 1;
        fs.copyFileSync(candidate, path.join(directory, 'history', `v${version}.py`));
        fs.renameSync(candidate, active);
        const entry = { version, activated_at: new Date().toISOString(), toolCallId: reference(id), rationale: input.rationale, evidence: input.evidence.map(reference), contract: JSON.parse(check.stdout) };
        fs.writeFileSync(path.join(directory, 'active.json'), JSON.stringify(entry, null, 2));
        fs.writeFileSync(path.join(directory, 'history', `v${version}.json`), JSON.stringify(entry, null, 2));
        acceptedThisRun += 1;
        setTools();
        log('runtime_revision_activated', entry);
        return { content: [{ type: 'text', text: `Runtime revision ${version} passed the contract and is active now. Use inspect_data on a task input to verify it in the ongoing task.` }], details: entry };
      } catch (error) {
        const reason = String(error.stdout || '') + '\n' + String(error.stderr || error.message);
        const failure = { toolCallId: reference(id), rationale: input.rationale, reason: reason.slice(-4000) };
        log('runtime_revision_rejected', failure);
        const name = `rejected-${reference(id).replace(/[^a-zA-Z0-9_-]/g, '')}`;
        fs.copyFileSync(candidate, path.join(directory, 'history', `${name}.py`));
        fs.writeFileSync(path.join(directory, 'history', `${name}.json`), JSON.stringify(failure, null, 2));
        throw new Error('Runtime candidate rejected by its API contract:\n' + reason.slice(-4000));
      }
    },
  });
}
