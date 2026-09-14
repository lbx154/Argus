import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { copyVerified } from '../scripts/verified-copy.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-copy-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test('staging copies binary, empty and Unicode-named nested files without losing bytes', t => {
  const root = fixture(t), source = path.join(root, 'source'), target = path.join(root, 'target');
  fs.mkdirSync(path.join(source, 'nested'), { recursive: true });
  const bytes = Buffer.from(Array.from({ length: 2 * 1024 * 1024 + 37 }, (_, i) => i % 251));
  fs.writeFileSync(path.join(source, 'nested', '资料.bin'), bytes);
  fs.writeFileSync(path.join(source, 'empty'), '');
  const result = copyVerified(source, target);
  assert.deepEqual(fs.readFileSync(path.join(target, 'nested', '资料.bin')), bytes);
  assert.equal(fs.statSync(path.join(target, 'empty')).size, 0);
  assert.equal(result.files, 2);
  assert.equal(result.bytes, bytes.length);
  assert.equal(result.sha256.length, 64);
  assert.deepEqual(copyVerified(source, path.join(root, 'another')), result);
});

test('single-file digest describes actual destination bytes', t => {
  const root = fixture(t), source = path.join(root, 'input'), target = path.join(root, 'output');
  const bytes = Buffer.from([0, 80, 75, 255, 0, 17]);
  fs.writeFileSync(source, bytes);
  const result = copyVerified(source, target);
  assert.equal(result.sha256, createHash('sha256').update(bytes).digest('hex'));
  assert.equal(result.bytes, bytes.length);
});

test('staging refuses to overwrite an existing destination', t => {
  const root = fixture(t), source = path.join(root, 'input'), target = path.join(root, 'output');
  fs.writeFileSync(source, 'new'); fs.writeFileSync(target, 'keep');
  assert.throws(() => copyVerified(source, target), /already exists/);
  assert.equal(fs.readFileSync(target, 'utf8'), 'keep');
});

test('staging rejects directory junctions rather than following another root', t => {
  const root = fixture(t), directory = path.join(root, 'data'), linked = path.join(root, 'linked');
  fs.mkdirSync(directory);
  try { fs.symlinkSync(directory, linked, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Host cannot create a link'); throw error; }
  assert.throws(() => copyVerified(linked, path.join(root, 'copy')), /linked build inputs/);
});
