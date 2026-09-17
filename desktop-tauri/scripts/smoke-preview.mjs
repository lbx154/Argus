import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';
import { createReadingFixture, verifyReadingExperience } from './preview-reading-checks.mjs';
import { verifyRuntimeStability } from './preview-stability-checks.mjs';
import { verifyColdStartupTiming, verifyEyeMotion } from './preview-eye-checks.mjs';
import { windowsMotionPreference } from './windows-motion.mjs';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const binary = resolve(process.argv[2] || '');
assert(process.platform === 'win32' && existsSync(binary), 'Pass a staged Windows preview Argus.exe.');
const stage = dirname(binary);
const systemMotionBefore = windowsMotionPreference();
const soakIndex = process.argv.indexOf('--soak-seconds');
const soakSeconds = soakIndex < 0 ? 0 : Number(process.argv[soakIndex + 1]);
assert(Number.isInteger(soakSeconds) && soakSeconds >= 0, 'Invalid soak duration.');
assert(existsSync(join(stage, 'WebView2Loader.dll')), 'WebView2 loader must be beside the host.');
assert(existsSync(join(stage, 'argus-backend', 'argus-backend.exe')), 'Frozen backend missing.');
// Scientific workspaces must not live inside source or application directories.
const sandbox = mkdtempSync(join(tmpdir(), 'argus-preview-smoke-'));
console.log(`Isolated native QA workspace: ${sandbox}`);
const appData = join(sandbox, 'appdata');
const localData = join(sandbox, 'localappdata');
const previewData = join(appData, 'argus-desktop-preview-integration-20260913');
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
const cleanEnvironment = { ...process.env };
for (const key of Object.keys(cleanEnvironment)) {
  if (/API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|^ARGUS_|^PI_/i.test(key)) delete cleanEnvironment[key];
}
const isolatedHome = join(sandbox, 'user-home');
mkdirSync(isolatedHome, { recursive: true });
const env = {
  ...cleanEnvironment,
  HOME: isolatedHome, USERPROFILE: isolatedHome,
  APPDATA: appData,
  LOCALAPPDATA: localData,
  // Keep a real single-instance mutex, but never activate an operator's old
  // preview. The same namespace is reused for this test's second launch.
  ARGUS_DESKTOP_TEST_INSTANCE: randomUUID().replaceAll('-', ''),
  // Deliberately point at another home: preview must ignore production overrides.
  ARGUS_SKILL_HOME: join(sandbox, 'do-not-touch-home'),
  ARGUS_DESKTOP_DEV: '1',
  ARGUS_DESKTOP_REPO_ROOT: join(sandbox, 'must-not-use-source'),
  ARGUS_SKILL_RUNNER_BACKEND: 'codex',
  ARGUS_SKILL_RUNNER_BIN: codex,
  CODEX_HOME: join(sandbox, 'unused-codex-home'),
  COPILOT_HOME: join(sandbox, 'unused-copilot-home'),
  CLAUDE_CONFIG_DIR: join(sandbox, 'unused-claude-home'),
  PI_CODING_AGENT_DIR: join(sandbox, 'unused-pi-home'),
  XDG_CONFIG_HOME: join(sandbox, 'config'), XDG_CACHE_HOME: join(sandbox, 'cache'),
  PATH: `${join(process.env.SystemRoot || 'C:\\Windows', 'System32')};${dirname(codex)}`,
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort} --remote-debugging-address=127.0.0.1`,
  PYTHONUTF8: '1',
  PYTHONIOENCODING: 'utf-8',
};
for (const key of ['ARGUS_DESKTOP_DISABLE_SINGLE_INSTANCE', 'ARGUS_WORKBENCH_HOST_ROOT', 'NVM_HOME', 'NVM_SYMLINK']) delete env[key];

// A stopped, synthetic project with recorded tasks exercises the actual frozen
// map API/UI without running a daemon or spending any provider/model credit.
const seed = `from pathlib import Path
import time
from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import append_turn
from argus.life.memory import BacklogItem, LifeMemory
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
      connected = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, { timeout: 800, noDefaults: true });
      break;
    } catch { await delay(250); }
  }
  assert(connected, 'The actual preview WebView2 did not become available.');
  browser = connected;
  const context = browser.contexts()[0];
  // Deny every non-read API request except the synthetic attachment upload.
  // This covers new Reader/Advisor/daemon endpoints as well as map summaries.
  await context.route(`http://127.0.0.1:${apiPort}/api/**`, async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const read = ['GET', 'HEAD', 'OPTIONS'].includes(request.method());
    const fixtureUpload = request.method() === 'POST' && pathname === '/api/projects/s-preview-smoke/attachments';
    if (read || fixtureUpload) return route.continue();
    modelRequests++;
    console.error(`Blocked unexpected native QA mutation: ${request.method()} ${pathname}`);
    await route.abort('blockedbyclient');
  });
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
async function assertNativeMotionBaseline(page) {
  const reduced = await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  assert.equal(reduced, systemMotionBefore.reducedMotion, 'WebView media preference differs from the real Windows setting.');
  return reduced;
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
  const firstAppearance = await invoke(page, 'get_appearance');
  assert.equal(firstAppearance.theme, 'light', 'A fresh native profile must default to light.');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  assert.equal(firstAppearance.startupEyeMotion, undefined, 'Eye animation is no longer a stored preference.');
  await expect(page.locator('html')).toHaveAttribute('data-startup-eye-motion', 'on');
  await expect(page.locator('button[data-startup-eye-motion]')).toHaveCount(0);
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(249, 250, 251)');
  await assertNativeMotionBaseline(page);
  record('Fresh profile starts light; native attachment uses noDefaults and never emulates media or focus');
  // No page.emulateMedia anywhere in this native smoke: even a color-only call
  // would silently set reducedMotion=no-preference. Cross-check the OS instead.
  const originalEye = await verifyEyeMotion(page, '.argus-wizard-eye', { directory: stage, label: 'native-first-run', moving: true });
  assert.equal(originalEye.reducedMotion, systemMotionBefore.reducedMotion);
  record(`Native first-run pupil moves with Windows reduced-motion=${originalEye.reducedMotion}, application eye=on`);
  if (systemMotionBefore.reducedMotion) {
    const transition = await page.locator('.desktop-menu-trigger').first().evaluate(element => parseFloat(getComputedStyle(element).transitionDuration));
    assert(transition < 0.001, 'Only the eye may override reduced motion, not the rest of the UI.');
  }
  assert.equal(await page.locator('.argus-loading-ring').count(), 0, 'Do not add arcs or other decorations to the original eye.');
  await expect(page.locator('#trialOpen')).toBeVisible();
  await page.locator('#trialOpen').click();
  await page.locator('#trialKey').fill('invalid-preview-key');
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#trialProgress')).toContainText('完整的内部测试 Key');
  await page.locator('#ownOpen').click();
  record('Original eye geometry retained under the real OS preference; malformed trial keys rejected');
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

  // A frozen daemon must receive the installation root from the product host,
  // not from an acceptance script's ambient build environment.
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
  await page.locator('#fileMenuTrigger').click();
  await expect(page.locator('button[data-startup-eye-motion]')).toHaveCount(0);
  await page.locator('[data-menu-action="settings"]').click();
  await expect(page.locator('#wizard')).toBeVisible();
  await verifyEyeMotion(page, '.argus-wizard-eye', { directory: stage, label: 'native-fixed-on-settings', moving: true });
  await page.locator('#wizardCancel').click();
  assert.equal((await invoke(page, 'get_status')).pid, state.pid);
  record('Eye animation is fixed on; settings contain no eye controls and do not restart the backend');
  assert.equal((await request('/api/plugins', false)).status, 401);
  const pluginRows = await (await request('/api/plugins')).json();
  assert.equal(pluginRows.plugins[0].id, 'crystalpilot');
  assert.equal(pluginRows.plugins[0].installed, false);
  assert.equal(pluginRows.plugins[0].enabled, false);
  assert.equal(pluginRows.plugins[0].version, '0.4.0');
  record('Packaged plugin center lists pinned CrystalPilot 0.4.0, disabled by default; management API is authenticated');
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

  const assetDir = join(stage, 'argus-backend', '_internal', 'argus', '_frontend', 'web', 'dist', 'assets');
  const mapChunk = readdirSync(assetDir).find((name) => name.startsWith('MapPanel-') && name.endsWith('.js'));
  assert(mapChunk, 'Packaged MapPanel chunk is missing.');
  const moduleCheck = await frame.evaluate(async (name) => {
    try {
      const component = (await import(`/assets/${name}`)).MapPanel;
      return { ok: typeof component === 'function' || (component?.$$typeof === Symbol.for('react.memo') && typeof component.type === 'function') };
    } catch (error) { return { ok: false, error: String(error?.stack || error) }; }
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
  await assertNativeMotionBaseline(page);
  await verifyEyeMotion(page, '.argus-splash-eye', {
    directory: stage, label: 'native-exe-reactivation', duration: 350, minimumExcursion: 0.02, moving: true,
  });
  await verifyColdStartupTiming(page);
  await expect(page.locator('#splash')).toBeHidden();
  record('Native maximize/restore and explicit EXE reactivation replay visible eye pixels while retaining the authenticated backend and live iframe');

  await delay(16_000);
  assert.equal((await invoke(page, 'get_status')).state, 'ready');
  const log = readFileSync(join(previewData, 'logs', 'desktop.log'), 'utf8');
  assert(log.includes('runner preflight passed:'), 'Real Codex --version preflight did not pass.');
  assert(!log.includes('backend health probe transient failure'), 'Health checks stalled.');
  record('Stable backend health and real CLI preflight with PATH restricted to Windows system and the selected CLI directory');

  await verifyRuntimeStability({ page, frame, stage, dataDir: previewData,
    native: (command, args) => invoke(page, command, args), request, reveal: revealHost,
    seconds: soakSeconds, record });

  await frame.evaluate(() => localStorage.setItem('argus.workspace.view', 'activity'));
  const savedAppearance = await invoke(page, 'set_appearance', { input: { theme: 'dark' } });
  assert.equal(savedAppearance.theme, 'dark');
  await quitHost(page);
  record('Explicit stop-and-quit exits the owned host/backend');
  page = await startHost();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  assert.equal((await invoke(page, 'get_appearance')).theme, 'dark', 'An explicit saved theme must survive restart.');
  record('A previously chosen dark theme survives a real native process restart');
  const coldReduced = await assertNativeMotionBaseline(page);
  await expect(page.locator('html')).toHaveAttribute('data-startup-eye-motion', 'on');
  await verifyEyeMotion(page, '.argus-splash-eye', {
    directory: stage, label: 'native-cold-start', duration: 400, minimumExcursion: 0.02, moving: true,
  });
  const coldTiming = await verifyColdStartupTiming(page);
  writeFileSync(join(stage, 'native-cold-start-timing.json'), JSON.stringify(coldTiming, null, 2) + '\n', { flag: 'wx' });
  frame = await cockpit(page);
  await expect(page.locator('#wizard')).toBeHidden();
  record(`Configured native cold start: visible eye ${coldTiming.visibleMilliseconds.toFixed(0)}ms, actual Windows reduced-motion=${coldReduced}, eye=on; saved CLI reused`);
  await quitHost(page);
  const systemMotionAfter = windowsMotionPreference();
  assert.deepEqual(systemMotionAfter, systemMotionBefore, 'System animation preference must not be changed.');
  writeFileSync(join(stage, 'native-motion-baseline.json'), JSON.stringify({
    noDefaults: true, mediaEmulationUsed: false, focusEmulationUsed: false,
    systemBefore: systemMotionBefore, systemAfter: systemMotionAfter,
    firstRun: { systemReducedMotion: originalEye.reducedMotion, eyeMode: originalEye.eyeMode, moving: originalEye.expectedMoving },
    coldOn: coldTiming, fixedOn: true,
  }, null, 2) + '\n', { flag: 'wx' });
  assert.deepEqual(readdirSync(production), ['do-not-touch.txt']);
  assert(!existsSync(join(sandbox, 'do-not-touch-home')));
  assert(existsSync(join(previewData, 'webview')));
  assert.equal(errors.length, 0, `WebView uncaught errors: ${errors.join('; ')}`);
  assert.equal(modelRequests, 0, 'Native QA must not attempt Reader, Advisor, model, daemon or other unapproved API mutations.');
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
