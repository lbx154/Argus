import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { packagePortable } from '../scripts/desktop-portable.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-portable-fixture-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const desktopRoot = path.join(root, 'desktop'), releaseDir = path.join(root, 'build'), outputDir = path.join(root, 'new portable');
  const write = (filename, value) => { fs.mkdirSync(path.dirname(filename), { recursive: true }); fs.writeFileSync(filename, value); };
  const resources = {
    'payload/node-runtime': 'node-runtime', 'payload/argus-core': 'argus-core', 'payload/WebView2Loader.dll': 'WebView2Loader.dll',
  };
  write(path.join(desktopRoot, 'src-tauri/tauri.conf.json'), JSON.stringify({ bundle: { resources } }));
  write(path.join(desktopRoot, 'src-tauri/tauri.windows.conf.json'), JSON.stringify({ bundle: { resources: {} } }));
  write(path.join(desktopRoot, 'src-tauri/tauri.preview.conf.json'), JSON.stringify({ identifier: 'cn.argusbot.desktop.preview.v1', version: '1.0.0-preview.2', bundle: { createUpdaterArtifacts: false } }));
  for (const relative of ['node-runtime/node.exe', 'node-runtime/LICENSE', 'argus-core/dist/argus.mjs', 'argus-core/web/index.html', 'WebView2Loader.dll']) {
    write(path.join(desktopRoot, 'src-tauri/payload', relative), `synthetic ${relative}`);
  }
  write(path.join(releaseDir, 'Argus.exe'), 'synthetic executable, never executed');
  return { root, desktopRoot, releaseDir, outputDir };
}

test('portable is a full verified resource layout, not a renamed installer or bare EXE', t => {
  const f = fixture(t); const result = packagePortable(f);
  assert.equal(result.installer, false); assert.equal(result.updatesEnabled, false);
  assert.equal(fs.existsSync(path.join(f.outputDir, 'latest.json')), false);
  assert.ok(fs.readFileSync(path.join(f.outputDir, '开始使用.txt'), 'utf8').includes('双击 Argus.exe'));
  const sums = fs.readFileSync(path.join(f.outputDir, 'SHA256SUMS.txt'), 'utf8').trim().split('\n');
  for (const line of sums) {
    const [sha, filename] = line.split('  ');
    assert.equal(createHash('sha256').update(fs.readFileSync(path.join(f.outputDir, filename))).digest('hex'), sha);
  }
  assert.equal(sums.length, 6);
});

test('an earlier portable directory is never overwritten', t => {
  const f = fixture(t); packagePortable(f);
  fs.writeFileSync(path.join(f.outputDir, 'keep.txt'), 'reviewed preview');
  assert.throws(() => packagePortable(f), /already exists/);
  assert.equal(fs.readFileSync(path.join(f.outputDir, 'keep.txt'), 'utf8'), 'reviewed preview');
});

test('missing core resources fail before output creation', t => {
  const f = fixture(t);
  fs.renameSync(path.join(f.desktopRoot, 'src-tauri/payload/argus-core/web/index.html'), path.join(f.root, 'held-fixture.html'));
  assert.throws(() => packagePortable(f), /Missing portable resource/);
  assert.equal(fs.existsSync(f.outputDir), false);
});

test('production identity and updater signing are refused by portable packaging', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.desktopRoot, 'src-tauri/tauri.preview.conf.json'), JSON.stringify({ identifier: 'cn.argusbot.desktop', version: '1.0.0', bundle: { createUpdaterArtifacts: true } }));
  assert.throws(() => packagePortable(f), /separate unsigned preview/);
  assert.equal(fs.existsSync(f.outputDir), false);
});
