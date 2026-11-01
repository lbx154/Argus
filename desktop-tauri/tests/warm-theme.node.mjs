import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = file => readFileSync(new URL(file, import.meta.url), 'utf8');
const web = read('../../frontend/web/src/index.css');
const shell = read('../src/style.css');
const block = (css, dark = false) => {
  const match = (dark ? /:root\[data-theme='dark'\]\s*\{([^}]+)\}/ : /:root\s*\{([^}]+)\}/).exec(css);
  assert.ok(match);
  return Object.fromEntries([...match[1].matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, key, value]) => [key, value.trim()]));
};
const hex = rgb => '#' + rgb.split(/\s+/).map(value => Number(value).toString(16).padStart(2, '0')).join('');

test('light cockpit is one warm canvas, with ivory for raised surfaces', () => {
  const p = block(web);
  assert.equal(p['--surface-0'], '247 243 232');
  assert.equal(p['--surface-1'], p['--surface-0']);
  assert.equal(p['--surface-2'], '238 232 216');
  assert.equal(p['--surface-3'], '252 250 244');
  for (const key of ['--conversation-argus', '--glass', '--chrome-surface']) assert.equal(p[key], 'var(--surface-1)');
  assert.equal(p['--glass-raised'], 'var(--surface-3)');
  assert.equal(p['--conversation-user'], p['--surface-2']);
});

test('native shell and web share light and dark colours instead of separate palettes', () => {
  for (const dark of [false, true]) {
    const w = block(web, dark), s = block(shell, dark);
    assert.equal(s['--bg'], hex(w['--surface-0']));
    assert.equal(s['--chrome-bg'], hex(w['--surface-1']));
    assert.equal(s['--tile'], hex(w[dark ? '--surface-1' : '--surface-3']));
    assert.equal(s['--ink'], hex(w['--ink']));
    assert.equal(s['--accent'], hex(w['--accent-ink']));
    assert.equal(s['--danger'], hex(w['--err']));
  }
});

test('initial native window does not flash a different light canvas', () => {
  const config = JSON.parse(read('../src-tauri/tauri.conf.json'));
  assert.equal(config.app.windows[0].backgroundColor, block(shell)['--bg']);
  const native = read('../src-tauri/src/lib.rs');
  assert.match(native, /AppearanceTheme::Light[^\n]*Color\(247, 243, 232, 255\)/);
  assert.match(native, /AppearanceTheme::Dark[^\n]*Color\(12, 12, 13, 255\)/);
});

test('DWM caption, caption text and border match shell colours in both themes', () => {
  const native = read('../src-tauri/src/lib.rs');
  const caption = native.slice(native.indexOf('fn apply_windows_caption_palette'), native.indexOf('fn apply_window_appearance'));
  for (const [theme, dark] of [['Light', false], ['Dark', true]]) {
    const arm = new RegExp(`AppearanceTheme::${theme} => \\(([\\s\\S]*?)\\n        \\),`).exec(caption)?.[1];
    assert.ok(arm, `${theme} caption palette`);
    const colours = [...arm.matchAll(/colorref\((\d+), (\d+), (\d+)\)/g)].map(([, r, g, b]) => hex(`${r} ${g} ${b}`));
    const p = block(shell, dark);
    assert.deepEqual(colours, [p['--chrome-bg'], p['--ink'], p['--chrome-border']]);
  }
  assert.match(caption, /DWMWA_CAPTION_COLOR, caption/);
  assert.match(caption, /DWMWA_TEXT_COLOR, text/);
  assert.match(caption, /DWMWA_BORDER_COLOR, border/);
});

test('role picker uses shared control/panel radii and surfaces, not a standalone theme', () => {
  const css = read('../../frontend/web/src/components/roleModelPicker.css');
  assert.match(css, /\.role-route-tabs > button[^\n]*border-radius: var\(--radius-panel\)/);
  assert.match(css, /\.role-picker select[^\n]*border-radius: var\(--radius-control\)/);
  assert.match(css, /\.role-model-list[^\n]*background: rgb\(var\(--surface\)\)/);
  assert.doesNotMatch(css, /#[\da-f]{3,8}\b|background:\s*white\b/i);
});
