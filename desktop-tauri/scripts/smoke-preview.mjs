import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';
import { createReadingFixture, verifyReadingExperience } from './preview-reading-checks.mjs';
import { verifyRuntimeStability } from './preview-stability-checks.mjs';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const binary = resolve(process.argv[2] || '');
assert(process.platform === 'win32' && existsSync(binary), 'Pass a staged Windows preview Argus.exe.');
const stage = dirname(binary);
const soakIndex = process.argv.indexOf('--soak-seconds');
const soakSeconds = soakIndex < 0 ? 0 : Number(process.argv[soakIndex + 1]);
assert(Number.isInteger(soakSeconds) && soakSeconds >= 0, 'Invalid soak duration.');
assert(existsSync(join(stage, 'WebView2Loader.dll')), 'WebView2 loader must be beside the host.');
assert(existsSync(join(stage, 'argus-backend', 'argus-backend.exe')), 'Frozen backend missing.');
const sandbox = mkdtempSync(join(desktop, 'build', 'preview-smoke-'));
const appData = join(sandbox, 'appdata');
const localData = join(sandbox, 'localappdata');
const previewData = join(appData, 'argus-desktop-preview');
const home = join(previewData, 'argus-home');
const workspace = join(sandbox, 'reading-workspace');
createReadingFixture(workspace);
const production = join(appData, 'argus-desktop');
mkdirSync(production, { recursive: true });
writeFileSync(join(production, 'do-not-touch.txt'), 'production sentinel');
mkdirSync(join(localData, 'nvm'), { recursive: true });
const which = (name) => {
  const result = spawnSync('where.exe', [name], { encoding: 'utf8' });
  assert.equal(result.status, 0, `${name} is needed for the real CLI startup smoke.`);
  return result.stdout.trim().split(/\r?\n/)[0];
};
const codex = which('codex.cmd');
const node = which('node.exe');
writeFileSync(join(localData, 'nvm', 'settings.txt'), `path: ${dirname(node)}\n`);

async function freePort() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise((resolveClose) => server.close(resolveClose));
  return port;
}
const apiPort = await freePort();
const debugPort = await freePort();
const env = {
  ...process.env,
  APPDATA: appData,
  LOCALAPPDATA: localData,
  // Deliberately point at another home: preview must ignore production overrides.
  ARGUS_SKILL_HOME: join(sandbox, 'do-not-touch-home'),
  ARGUS_DESKTOP_DEV: '1',
  ARGUS_DESKTOP_REPO_ROOT: join(sandbox, 'must-not-use-source'),
  ARGUS_SKILL_RUNNER_BACKEND: 'codex',
  ARGUS_SKILL_RUNNER_BIN: codex,
  PATH: `${join(process.env.SystemRoot || 'C:\\Windows', 'System32')};${dirname(codex)}`,
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort} --remote-debugging-address=127.0.0.1`,
  PYTHONUTF8: '1',
  PYTHONIOENCODING: 'utf-8',
};
for (const key of ['ARGUS_DESKTOP_DISABLE_SINGLE_INSTANCE', 'NVM_HOME', 'NVM_SYMLINK']) delete env[key];

// A stopped, synthetic project with recorded tasks exercises the actual frozen
// map API/UI without running a daemon or spending any provider/model credit.
const seed = `from pathlib import Path
import time
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.core.transcript import append_turn
from argus_skill.life.memory import BacklogItem, LifeMemory
root = Path(${JSON.stringify(home)})
workspace = ${JSON.stringify(workspace)}
now = time.time()
write_session_meta(root, SessionMeta(id='s-preview-smoke', created=now, last_active=now, display_name='Desktop smoke project', workdir=workspace, cwd=workspace))
life = root / 'projects' / 's-preview-smoke'
mem = LifeMemory.open(life)
mem.backlog.add(BacklogItem(id='task-smoke', ts=now, title='Desktop map verification', objective='Verify packaged map', status='done'))
target = {'path': 'preview.pdf', 'label': 'preview.pdf', 'source': 'reviewer_evidence', 'why': 'Synthetic read-only UI fixture'}
receipt = {'schema_version': 1, 'delivery_id': 'fixture-reading', 'kind': 'task_completed', 'item_id': 'task-smoke', 'title': 'PDF reading fixture', 'summary': 'Synthetic preview test document.', 'status': 'done', 'review_status': 'done', 'delivered_at': now, 'targets': [target], 'primary_target': target}
assert append_turn(life, 'argus', 'Preview test document: [preview.pdf](preview.pdf)', metadata={'delivery': receipt})
`;
const seeded = spawnSync(join(stage, 'argus-backend', 'argus-backend.exe'), ['-c', seed], { env, encoding: 'utf8', timeout: 60_000 });
assert.equal(seeded.status, 0, `Could not seed isolated fixture: ${seeded.stderr}`);

let child;
let browser;
const ownedChildren = [];
let modelRequests = 0;
const checks = [];
const errors = [];
const record = (name) => { checks.push(name); console.log(`PASS ${name}`); };
async function startHost() {
  child = spawn(binary, [], { cwd: stage, env, stdio: 'ignore' });
  ownedChildren.push(child);
  let connected;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) throw new Error('Preview exited before connection; check WebView2 or another running preview instance.');
    try {
      connected = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, { timeout: 800 });
      break;
    } catch { await delay(250); }
  }
  assert(connected, 'The actual preview WebView2 did not become available.');
  browser = connected;
  const context = browser.contexts()[0];
  await context.addInitScript(() => {
    window.addEventListener('vite:preloadError', (event) => console.error('Preload failure:', String(event.payload?.stack || event.payload)));
  });
  let page;
  for (let attempt = 0; attempt < 80; attempt++) {
    page = context.pages().find((item) => item.url().includes('tauri.localhost') || item.url().startsWith('tauri://'));
    if (page) break;
    await delay(250);
  }
  assert(page, 'Trusted desktop shell was not created.');
  page.on('pageerror', (error) => {
    if (!errors.includes(error.message)) errors.push(error.message);
  });
  page.on('console', (message) => {
    if (message.type() === 'error' && message.text().startsWith('Preload failure:')) {
      const text = message.text();
      if (!errors.includes(text)) errors.push(text);
    }
  });
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/api/map-copy/')) modelRequests++;
  });
  page.on('dialog', (dialog) => dialog.accept());
  return page;
}
async function revealHost() {
  const second = spawn(binary, [], { cwd: stage, env, stdio: 'ignore' });
  ownedChildren.push(second);
  await Promise.race([once(second, 'exit'), delay(10_000).then(() => { throw new Error('Single-instance activation failed.'); })]);
}
async function quitHost(page) {
  await page.locator('#fileMenuTrigger').click();
  const exited = once(child, 'exit');
  await page.locator('[data-menu-action="stop-quit"]').click();
  await Promise.race([exited, delay(15_000).then(() => { throw new Error('Stop-and-quit did not exit.'); })]);
  browser = undefined;
}
async function invoke(page, command, args = {}) {
  return page.evaluate(({ name, input }) => window.__TAURI_INTERNALS__.invoke(name, input), { name: command, input: args });
}
async function cockpit(page) {
  await expect(page.locator('#cockpit')).toBeVisible({ timeout: 45_000 });
  let frame;
  for (let attempt = 0; attempt < 100; attempt++) {
    frame = page.frames().find((item) => item.url().startsWith(`http://127.0.0.1:${apiPort}/`));
    if (frame && await frame.locator('body').textContent()) break;
    await delay(200);
  }
  assert(frame, 'Authenticated cockpit iframe missing.');
  return frame;
}

try {
  let page = await startHost();
  await expect(page.locator('#wizard')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#wizardNext')).toBeDisabled();
  assert(!existsSync(join(previewData, 'runtime', 'backend.json')), 'Backend started before explicit CLI confirmation.');
  record('First launch requires CLI confirmation; no backend spawned beforehand');
  await page.screenshot({ path: join(stage, 'preview-onboarding.png'), animations: 'disabled' });
  await page.locator('[data-kind="codex"]').click();
  await page.locator('#wizardNext').click();
  await page.locator('#portInput').fill(String(apiPort));
  await page.locator('#wizardNext').click();
  await expect(page.locator('#summaryUrl')).toHaveText(`127.0.0.1:${apiPort}`);
  const started = Date.now();
  await page.locator('#wizardFinish').click();
  let frame = await cockpit(page);
  await expect(page.locator('#wizard')).toBeHidden();
  const remoteCanInvoke = await frame.evaluate(async () => {
    if (!window.__TAURI_INTERNALS__) return false;
    try {
      await window.__TAURI_INTERNALS__.invoke('get_setup');
      return true;
    } catch { return false; }
  });
  assert.equal(remoteCanInvoke, false, 'Remote cockpit must not invoke native desktop commands.');
  assert.equal(await page.evaluate(() => Object.isFrozen(Object.prototype)), true, 'Trusted shell must stay hardened.');
  assert.equal(await frame.evaluate(() => Object.isFrozen(Object.prototype)), false, 'Web iframe must retain standard browser prototype semantics.');
  record(`Actual frozen backend and isolated cockpit ready (${((Date.now() - started) / 1000).toFixed(1)}s)`);

  const state = await invoke(page, 'get_status');
  const auth = new URL(state.url).searchParams.get('token');
  const request = async (path, authenticated = true) => fetch(`http://127.0.0.1:${apiPort}${path}`, {
    headers: authenticated ? { Authorization: `Bearer ${auth}` } : {},
    signal: AbortSignal.timeout(10_000),
  });
  assert.equal((await request('/api/projects', false)).status, 401);
  const meta = await (await request('/api/meta')).json();
  assert.equal(meta.authentication.authenticated, true);
  assert.ok(meta.runtime.package_version, 'bundled backend reports its package version');
  const map = await request('/api/projects/s-preview-smoke/map');
  assert.equal(map.status, 200);
  assert.equal((await map.json()).tasks[0].id, 'task-smoke');
  record('Authenticated API, bundled source identity and new research-map endpoint');
  const shim = join(previewData, 'runtime', 'bin', 'python.cmd');
  const shimCheck = spawnSync('cmd.exe', ['/d', '/s', '/c', `""${shim}" -c "print('shim-ok')""`], {
    env: { ...env, ARGUS_SKILL_PYTHON: join(stage, 'argus-backend', 'argus-backend.exe') },
    encoding: 'utf8', timeout: 30_000, windowsVerbatimArguments: true,
  });
  assert.equal(shimCheck.status, 0, 'Frozen Python command shim failed (check Unicode/space path handling).');
  assert(shimCheck.stdout.includes('shim-ok'), 'Frozen Python command shim did not execute the requested code.');
  record('Frozen Python command shim works from the actual extracted path');

  // The actual Web bridge, not a mock: focus is inside the cross-origin frame.
  await frame.locator('body').click({ position: { x: 10, y: 10 } });
  await page.keyboard.press('Control+,');
  await expect(page.locator('#wizard')).toBeVisible();
  await page.locator('#wizardNext').click();
  const occupied = createServer();
  occupied.listen(0, '127.0.0.1');
  await once(occupied, 'listening');
  const occupiedPort = occupied.address().port;
  await page.locator('#portInput').fill(String(occupiedPort));
  await page.locator('#wizardNext').click();
  await page.locator('#wizardFinish').click();
  await expect(page.locator('#wizardError')).toContainText('已被占用');
  assert.equal((await request('/api/meta')).status, 200, 'Invalid settings interrupted the running backend.');
  await new Promise((resolveClose) => occupied.close(resolveClose));
  await page.locator('#wizardCancel').click();
  record('Web-focused Ctrl+, and occupied-port rejection without stopping the current backend');

  const update = await invoke(page, 'check_for_update', { manual: true });
  assert.equal(update.state, 'idle');
  await expect(page.locator('#updateTitle')).toHaveText('发布更新已禁用');
  await page.locator('#updateDismiss').click();
  assert(!existsSync(join(previewData, 'update-check.json')));
  record('Preview cannot check/download/install release updates');

  await verifyReadingExperience(page, frame, stage, record);

  const assetDir = join(stage, 'argus-backend', '_internal', 'argus_skill', '_frontend', 'web', 'dist', 'assets');
  const mapChunk = readdirSync(assetDir).find((name) => name.startsWith('MapPanel-') && name.endsWith('.js'));
  const moduleCheck = await frame.evaluate(async (name) => {
    try { return { ok: typeof (await import(`/assets/${name}`)).MapPanel === 'function' }; }
    catch (error) { return { ok: false, error: String(error?.stack || error) }; }
  }, mapChunk);
  assert(moduleCheck.ok, `Packaged map module failed to import: ${moduleCheck.error || 'missing MapPanel export'}`);
  // Use the real project, but kiosk mode prevents optional map-summary model calls.
  const viewUrl = new URL(state.url);
  viewUrl.searchParams.set('kiosk', '1');
  viewUrl.searchParams.set('view', 'map');
  viewUrl.searchParams.set('project', 's-preview-smoke');
  await frame.goto(viewUrl.toString());
  await expect(frame.locator('.map-canvas-wrap')).toBeVisible({ timeout: 30_000 });
  await expect(frame.locator('.react-flow__node').first()).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: join(stage, 'preview-research-map.png'), animations: 'disabled' });
  record('Latest map lazy-loaded assets render in packaged WebView2 (no model calls)');

  await invoke(page, 'set_large_preview', { active: true });
  assert.equal(await invoke(page, 'plugin:window|is_maximized', { label: 'main' }), true);
  await invoke(page, 'set_large_preview', { active: false });
  await invoke(page, 'hide_desktop');
  assert.equal(await invoke(page, 'plugin:window|is_visible', { label: 'main' }), false);
  await revealHost();
  await expect.poll(() => invoke(page, 'plugin:window|is_visible', { label: 'main' })).toBe(true);
  assert.equal((await invoke(page, 'get_status')).pid, state.pid);
  record('Native maximize/restore, hide-to-background and single-instance reactivation');

  await delay(16_000);
  assert.equal((await invoke(page, 'get_status')).state, 'ready');
  const log = readFileSync(join(previewData, 'logs', 'desktop.log'), 'utf8');
  assert(log.includes('runner preflight passed:'), 'Real Codex --version preflight did not pass.');
  assert(!log.includes('backend health probe transient failure'), 'Health checks stalled.');
  record('Stable backend health and real CLI preflight with no compiler/Node directory in PATH');

  await verifyRuntimeStability({ page, frame, stage, dataDir: previewData,
    native: (command, args) => invoke(page, command, args), request, reveal: revealHost,
    seconds: soakSeconds, record });

  await frame.evaluate(() => localStorage.setItem('argus.workspace.view', 'activity'));
  await quitHost(page);
  record('Explicit stop-and-quit exits the owned host/backend');
  page = await startHost();
  frame = await cockpit(page);
  await expect(page.locator('#wizard')).toBeHidden();
  record('Second actual process launch reuses the saved CLI without onboarding');
  await quitHost(page);
  assert.deepEqual(readdirSync(production), ['do-not-touch.txt']);
  assert(!existsSync(join(sandbox, 'do-not-touch-home')));
  assert(existsSync(join(previewData, 'webview')));
  assert.equal(errors.length, 0, `WebView uncaught errors: ${errors.join('; ')}`);
  assert.equal(modelRequests, 0, 'Smoke tests must not request model-generated map summaries.');
  record('Production data untouched; settings, projects and WebView storage isolated; no uncaught UI errors');
  writeFileSync(join(stage, 'TEST-RESULTS.txt'), `Argus desktop preview validation\n${new Date().toISOString()}\n\n${checks.map((name) => `PASS ${name}`).join('\n')}\n\nHost toolchain: ${process.env.RUSTUP_TOOLCHAIN || 'default'}\nTest fixtures are synthetic and stopped. Model generation was disabled with kiosk mode.\nNo provider authentication or paid research task was exercised.\nWindows 10 and a machine without WebView2 were not independently tested.\n`);
  console.log('Packaged preview end-to-end smoke passed.');
} catch (error) {
  const redact = (value) => String(value).replace(/([?&]token=)[^\s&"']+/gi, '$1***');
  console.error(redact(error?.stack || error));
  console.error('Uncaught UI errors:', errors.map(redact));
  const failedPage = browser?.contexts()[0]?.pages()[0];
  if (failedPage) {
    await failedPage.screenshot({ path: join(sandbox, 'failure.png'), animations: 'disabled' }).catch(() => undefined);
    for (const frame of failedPage.frames()) {
      const text = await frame.locator('body').innerText({ timeout: 2000 }).catch(() => 'unavailable');
      console.error('Visible fixture UI:', redact(text.slice(0, 3500)));
    }
  }
  process.exitCode = 1;
} finally {
  // Only processes created by this script may be stopped. Never use /IM or
  // remove an operator directory. Keep the isolated fixture for failure diagnosis.
  for (const owned of ownedChildren) {
    if (owned.exitCode === null) {
      spawnSync('taskkill.exe', ['/pid', String(owned.pid), '/t', '/f'], { stdio: 'ignore' });
    }
  }
  if (browser) await browser.close().catch(() => undefined);
}
