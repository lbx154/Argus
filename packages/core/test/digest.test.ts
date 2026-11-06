import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { findExecutable, mergeEnv } from '../src/commands.js';

// A real Windows run failed decoding UTF-8 events with Python's default GBK locale.
// Force UTF-8 mode off and turn implicit locale reads into errors on every platform.
for (const script of ['../skills/argus-digest/digest.py', '../starter/tools/argus-digest']) {
  test(`${script} reads UTF-8 records without depending on Python UTF-8 mode`, t => {
    const python = findExecutable(process.platform === 'win32' ? 'python' : 'python3');
    if (!python) { t.skip('Python 3 is optional; the core does not require it'); return; }
    const root = mkdtempSync(join(tmpdir(), 'argus digest 中文 '));
    t.after(() => rmSync(root, { force: true, recursive: true }));
    const workdir = join(root, 'workspace 中文');
    mkdirSync(join(workdir, '.system-proof'), { recursive: true });
    writeFileSync(join(workdir, '.system-proof', 'phase'), '中文阶段🧪', 'utf8');
    writeFileSync(join(root, 'project.json'), JSON.stringify({ status: 'idle', name: '项目🧪', workdir, tasks: [] }), 'utf8');
    writeFileSync(join(root, 'events.jsonl'), JSON.stringify({ ts: '2026-11-06T00:00:00Z', type: 'message', text: '中文🧪' }) + '\n', 'utf8');
    const result = spawnSync(python, ['-X', 'utf8=0', '-X', 'warn_default_encoding', '-W', 'error::EncodingWarning', fileURLToPath(new URL(script, import.meta.url)), root, '--json'], {
      env: mergeEnv(process.env, { PYTHONUTF8: '0', PYTHONIOENCODING: 'utf-8' }), encoding: 'utf8',
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).phase, '中文阶段🧪');
  });
}
