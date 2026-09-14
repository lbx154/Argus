import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { stageBackend } from '../scripts/stage-backend.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-backend-stage-fixture-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'source'), destination = path.join(root, 'destination');
  fs.mkdirSync(path.join(source, '_internal', 'data'), { recursive: true });
  fs.writeFileSync(path.join(source, 'argus-backend.exe'), 'synthetic executable, never run');
  fs.writeFileSync(path.join(source, '_internal', 'data', 'binary.bin'), Buffer.from([0, 80, 255, 0, 17]));
  return { root, source, destination };
}

test('backend staging preserves a source placeholder and verifies exact nested bytes', t => {
  const f = fixture(t); fs.mkdirSync(f.destination); fs.writeFileSync(path.join(f.destination, '.gitkeep'), '');
  const result = stageBackend(f);
  assert.equal(result.reused, false);
  assert.equal(fs.existsSync(path.join(f.destination, '.gitkeep')), true);
  assert.deepEqual(fs.readFileSync(path.join(f.destination, '_internal/data/binary.bin')), Buffer.from([0, 80, 255, 0, 17]));
  assert.equal(result.sha256.length, 64);
});

test('preparing the same frozen payload is idempotent without replacing files', t => {
  const f = fixture(t); stageBackend(f);
  const image = path.join(f.destination, 'argus-backend.exe'); const before = fs.statSync(image, { bigint: true });
  assert.equal(stageBackend(f).reused, true);
  const after = fs.statSync(image, { bigint: true });
  assert.equal(after.ino, before.ino); assert.equal(after.mtimeNs, before.mtimeNs);
});

test('an older or unrelated prepared payload is preserved rather than erased', t => {
  const f = fixture(t); fs.mkdirSync(f.destination);
  const image = path.join(f.destination, 'argus-backend.exe'); fs.writeFileSync(image, 'keep old package');
  assert.throws(() => stageBackend(f), /Existing files were preserved/);
  assert.equal(fs.readFileSync(image, 'utf8'), 'keep old package');
  assert.equal(fs.existsSync(path.join(f.destination, '_internal')), false);
});

test('staging does not drop unexpected files to make a payload look current', t => {
  const f = fixture(t); stageBackend(f); fs.writeFileSync(path.join(f.destination, 'unexpected.bin'), 'keep');
  assert.throws(() => stageBackend(f), /Existing files were preserved/);
  assert.equal(fs.readFileSync(path.join(f.destination, 'unexpected.bin'), 'utf8'), 'keep');
});

test('staging rejects input/output overlap', t => {
  const f = fixture(t);
  assert.throws(() => stageBackend({ source: f.source, destination: path.join(f.source, 'nested') }), /separate/);
});

test('linked backend payloads do not escape the reviewed source tree', t => {
  const f = fixture(t); const outside = path.join(f.root, 'outside'); fs.mkdirSync(outside);
  try { fs.symlinkSync(outside, path.join(f.source, 'linked'), process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Host cannot create links'); throw error; }
  assert.throws(() => stageBackend(f), /linked payloads/);
  assert.equal(fs.existsSync(f.destination), false);
});
