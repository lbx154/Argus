import assert from 'node:assert/strict';
import test from 'node:test';
import os from 'node:os';
import { syncBuiltinESMExports } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { instructions } from '../src/prompts.js';
import { Store } from '../src/store.js';

test('standing context keeps locations, date, session lifetime and project GPU visibility', t => {
  const home = mkdtempSync(join(os.tmpdir(), 'argus-prompts-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  const old = process.env.CUDA_VISIBLE_DEVICES;
  t.after(() => { if (old === undefined) delete process.env.CUDA_VISIBLE_DEVICES; else process.env.CUDA_VISIBLE_DEVICES = old; });
  for (const backend of ['codex', 'claude', 'copilot', 'pi'] as const) {
    project.backend = backend;
    for (const visible of [undefined, '', '2,3']) {
      if (visible === undefined) delete process.env.CUDA_VISIBLE_DEVICES; else process.env.CUDA_VISIBLE_DEVICES = visible;
      const prompt = instructions('manager', project, store);
      assert.ok(prompt.includes(`Workspace: ${home}`));
      assert.ok(prompt.includes(`Argus's record of this project: ${store.dir(project.id)}`));
      assert.ok(prompt.includes(`team's in ${join(home, 'skills')}`));
      assert.match(prompt, /Today is \d{4}-\d{2}-\d{2} \(UTC\)\./);
      assert.ok(prompt.includes(['codex', 'claude'].includes(backend)
        ? 'Your session stays open between your turns' : 'Your session ends with each turn'));
      assert.ok(prompt.includes(`CUDA_VISIBLE_DEVICES=${visible === undefined ? '(unset)' : visible || '(none)'}`));
    }
  }
});

test('the machine is described with its load, free CPUs, memory and project GPUs', async t => {
  const home = mkdtempSync(join(os.tmpdir(), 'argus-machine-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  t.mock.method(os, 'cpus', () => Array(16).fill({}));
  t.mock.method(os, 'totalmem', () => 32 * 2 ** 30);
  const visible = process.env.CUDA_VISIBLE_DEVICES;
  t.after(() => { if (visible === undefined) delete process.env.CUDA_VISIBLE_DEVICES; else process.env.CUDA_VISIBLE_DEVICES = visible; });
  process.env.CUDA_VISIBLE_DEVICES = '2,3';
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  for (const [load, free] of [[3.25, 13], [23, 0]] as const) {
    t.mock.method(os, 'loadavg', () => [load, 4, 5]);
    syncBuiltinESMExports();
    const expected = `16 logical CPUs (load ${load.toFixed(1)}, about ${free} free), 32 GiB of memory; project GPUs: CUDA_VISIBLE_DEVICES=2,3.`;
    assert.equal(instructions('engineer', project, store).split('\n').at(-1), expected);
    const lines: string[] = [];
    const logging = t.mock.method(console, 'log', (line: string) => lines.push(line));
    try { await import(new URL(`../skills/research/machine.mjs?load=${load}`, import.meta.url).href); }
    finally { logging.mock.restore(); }
    assert.equal(lines[0], `16 logical CPUs (load ${load.toFixed(1)}, about ${free} free)`);
  }
});
