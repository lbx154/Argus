import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
const text = path => readFileSync(new URL(path, import.meta.url), 'utf8');

test('preview bundle identity matches the native fail-closed guard and cannot issue updates', () => {
  const preview = JSON.parse(text('../src-tauri/tauri.preview.conf.json'));
  const stable = JSON.parse(text('../src-tauri/tauri.conf.json'));
  const guard = /const PREVIEW_IDENTIFIER: &str = "([^"]+)"/.exec(text('../src-tauri/src/lib.rs'))?.[1];
  assert.equal(preview.identifier, guard);
  assert.notEqual(preview.identifier, stable.identifier);
  assert.notEqual(preview.productName, stable.productName);
  assert.equal(preview.bundle.createUpdaterArtifacts, false);
  assert.equal(stable.identifier, 'cn.argusbot.desktop');
  assert.equal(stable.plugins.updater.endpoints[0], 'https://github.com/lbx154/Argus/releases/latest/download/latest.json');
  assert.match(text('../scripts/desktop-preview.mjs'), /'--features', 'preview'/);
  assert.match(text('../src-tauri/src/updater.rs'), /!crate::release::preview_mode\(\)/);
});

test('all nine legacy desktop CLI names remain selectable and map to the TS core', () => {
  const types = text('../../packages/core/src/types.ts');
  const native = text('../src-tauri/src/models.rs');
  const ui = text('../src/index.html');
  for (const cli of ['codex', 'claude', 'copilot', 'cursor', 'pi', 'opencode', 'grok', 'qoder', 'dsh']) {
    assert.ok(types.includes(`'${cli}'`), cli);
    assert.ok(native.includes(`=> "${cli}"`), cli);
    assert.ok(ui.includes(`data-kind="${cli}"`), cli);
  }
});
