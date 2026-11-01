import assert from 'node:assert/strict';
import test from 'node:test';
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { pathToFileURL } from 'node:url';
import { dshOverlay } from '../src/agent/dsh.js';
import { models } from '../src/agent/models.js';

const run = promisify(execFile);

test('DSH still lists models when optional capability lookup never answers, and identifies failed providers', async t => {
  const root = await mkdtemp(join(tmpdir(), 'argus-catalog-budget-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stem = join(root, 'catalog');
  const patch = dshOverlay(stem, { catalog: true });
  const entries = JSON.parse(await readFile(patch, 'utf8')) as { id?: string; disabled?: boolean }[];
  assert.equal(entries.find(entry => entry.id === 'headless-runner')?.disabled, true, 'listing never starts an inference runner');
  assert.equal(entries.find(entry => entry.id === 'headless-startup')?.disabled, false, 'profile bootstrap must still complete');
  const driver = join(root, 'driver.mjs');
  await writeFile(driver, `import {apply} from ${JSON.stringify(pathToFileURL(`${stem}.dsh-observer.mjs`).href)};
const services = {
  loader: { await: async () => {} },
  llm: {
    listProviders: () => [{id:'good'}, {id:'unavailable'}],
    listModels: async provider => { if(provider === 'unavailable') throw new Error('offline'); return [{id:'one',name:'One'}, {id:'two',name:'Two'}]; },
    resolveModelInfo: async () => new Promise(() => {})
  },
  agentDefaultModel: { currentSelection: () => ({provider:'good',model:'one'}) },
  appExit: code => process.stdout.write('', () => process.exit(code))
};
apply({get: name => services[name]});
`);
  const { stdout } = await run(process.execPath, [driver], { timeout: 8000, windowsHide: true });
  const result = JSON.parse(stdout.trim().split('\n').at(-1)!) as { models: { model: string; efforts?: string[] }[]; failedProviders: string[] };
  assert.deepEqual(result.models.map(row => row.model), ['good/one', 'good/two']);
  assert.ok(result.models.every(row => row.efforts === undefined));
  assert.deepEqual(result.failedProviders, ['unavailable']);
});

test('a partial DSH catalog keeps its warning through cache/deduplication instead of masquerading as complete', async t => {
  const root = await mkdtemp(join(tmpdir(), 'argus-catalog-warning-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const script = join(root, 'dsh.mjs');
  await writeFile(script, `console.log(JSON.stringify({type:'argus_catalog',models:[{model:'good/one',default:true},{model:'good/one',default:true}],failedProviders:['unavailable']}));`);
  const previous = process.env.ARGUS_CLI_DSH_BIN;
  process.env.ARGUS_CLI_DSH_BIN = script;
  try {
    const result = await models('dsh', root, true);
    assert.equal(result.length, 1);
    assert.match(result.warning ?? '', /incomplete/);
    assert.equal((await models('dsh', root)).warning, result.warning);
  } finally {
    if (previous === undefined) delete process.env.ARGUS_CLI_DSH_BIN;
    else process.env.ARGUS_CLI_DSH_BIN = previous;
  }
});
