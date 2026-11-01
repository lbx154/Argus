// Exercise the real built Tauri executable and its bundled Node/WebView2, not a web mock.
// All desktop settings and browser data live in a new QA directory. No model inference.
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createServer } from 'node:net';
import { chromium } from 'playwright';
import { Store } from '../../packages/core/src/store.ts';

const root = resolve('..');
const home = mkdtempSync(join(root, '.preview-qa', 'packaged-'));
const roaming = join(home, 'roaming'), local = join(home, 'local');
mkdirSync(roaming); mkdirSync(local);
const store = new Store(join(roaming, 'argus-desktop-preview-1.0.0', 'argus-home'));
const project = store.create({ workdir: '', request: '', backend: 'pi', model: null });
const port = async () => { const server = createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); const p = server.address().port; await new Promise(resolve => server.close(resolve)); return p; };
const debugPort = await port(), apiPort = await port();
const exe = resolve(process.env.ARGUS_PREVIEW_EXE || 'src-tauri/target/release/Argus.exe');
const child = spawn(exe, [], { cwd: home, env: { ...process.env, APPDATA: roaming, LOCALAPPDATA: local,
  RUST_BACKTRACE: '1', WEBVIEW2_USER_DATA_FOLDER: join(home, 'webview'), WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort}` }, stdio: ['ignore', 'ignore', 'pipe'] });
let stderr = '';
child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-12_000); });
let exited = false;
child.on('exit', () => { exited = true; });
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, page;
try {
  for (let attempt = 0; ; attempt++) {
    try { const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`); if (response.ok) break; } catch {}
    assert.ok(!exited && attempt < 100, 'The packaged WebView2 did not start.'); await wait(200);
  }
  browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`);
  for (let attempt = 0; !page; attempt++) {
    // WebView2 first exposes a transient about:blank target while Tauri builds its window.
    page = browser.contexts().flatMap(context => context.pages()).find(page => !page.isClosed() && /tauri[.:]/.test(page.url()));
    assert.ok(!exited && attempt < 200, 'No stable native Tauri window'); if (!page) await wait(100);
  }
  await page.locator('#wizard').waitFor({ state: 'visible', timeout: 30_000 });
  assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), 'rgb(247, 243, 232)');
  await page.screenshot({ path: join(home, 'native-cream-setup.png') });
  await page.locator('[data-kind="pi"]').click();
  await page.locator('#wizardNext').click();
  await page.locator('#portInput').fill(String(apiPort));
  await page.locator('#wizardNext').click();
  await page.locator('#wizardFinish').click();
  await page.locator('#cockpit').waitFor({ state: 'visible', timeout: 45_000 });
  const setup = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('get_setup'));
  assert.equal(setup.releaseIdentity.distribution, 'preview');
  const status = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('get_status'));
  const address = new URL(status.url);
  const authentication = address.searchParams.get('token');
  const response = await fetch(`http://127.0.0.1:${apiPort}/api/meta`, { headers: { authorization: `Bearer ${authentication}` } });
  const meta = await response.json();
  assert.ok(meta.runtime.executable.includes('node-runtime'), 'The packaged application must use bundled Node.');
  assert.ok(meta.runtime.state_root.startsWith(home), 'Native state must remain isolated.');
  const frame = page.frameLocator('#cockpitFrame');
  await frame.locator('[data-runtime-open-models]').click({ timeout: 30_000 });
  await frame.locator('[data-role-tab="engineer"]').click();
  const available = Object.keys((await (await fetch(`http://127.0.0.1:${apiPort}/api/projects/${project.id}/config`, { headers: { authorization: `Bearer ${authentication}` } })).json()).available_backends).sort();
  assert.deepEqual((await frame.locator('[data-role-cli] option').evaluateAll(nodes => nodes.map(node => node.value).filter(Boolean))).sort(), available);
  assert.equal(await frame.locator('[data-role-price]').count(), 4);
  const catalogResponse = page.waitForResponse(response => response.url().includes('/model-choices?') && response.url().includes('backend=dsh'), { timeout: 65_000 });
  await frame.locator('[data-role-cli]').selectOption('dsh');
  const catalog = await (await catalogResponse).json();
  const safeError = String(catalog.error ?? '').replace(/(Bearer\s+|(?:api[_-]?key|password|secret|token)\s*[:=]\s*)\S+/gi, '$1***');
  assert.equal(safeError, '', `Packaged DSH model catalog: ${safeError}`);
  const chosen = catalog.options.find(option => option.default)?.model ?? catalog.options[0]?.model;
  assert.ok(chosen, 'The real DSH catalog must not be empty.');
  await frame.locator(`[data-role-model="${chosen}"] input`).check({ timeout: 10_000 });
  await page.screenshot({ path: join(home, 'native-role-models.png') });
  await frame.locator('[data-save-role-config]').click();
  await frame.locator('[data-role-model-picker]').waitFor({ state: 'hidden' });
  assert.equal(store.load(project.id).roleConfig.engineer.backend, 'dsh');
  const update = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('check_for_update', { manual: true }));
  assert.equal(update.state, 'idle');
  assert.ok(update.detail.includes('禁用'));
  writeFileSync(join(home, 'result.json'), JSON.stringify({ ok: true, executable: exe, bundledNode: meta.runtime.executable,
    stateRoot: meta.runtime.state_root, distribution: setup.releaseIdentity.distribution, updateDisabled: true, inferenceCalls: 0,
    creamSetup: true, onlyDiscoveredClis: available, independentPriceCards: 4, portable: !!process.env.ARGUS_PREVIEW_EXE }, null, 2));
  console.log(`PACKAGED_PREVIEW_ACCEPTANCE_PASSED=${home}`);
} catch (error) {
  console.error(`Native QA process: exited=${exited}, code=${child.exitCode}, evidence=${home}`);
  console.error(stderr.replace(/(Bearer\s+|(?:api[_-]?key|password|secret|token)\s*[:=]\s*)\S+/gi, '$1***'));
  if (page && !page.isClosed()) {
    const detail = await page.evaluate(async () => {
      const status = await window.__TAURI_INTERNALS__.invoke('get_status');
      return { status: { state: status.state, message: status.message, detail: status.detail },
        wizard: document.querySelector('#wizardError')?.textContent, splash: document.querySelector('#detail')?.textContent };
    }).catch(() => ({ error: 'Native diagnostics unavailable.' }));
    console.error(JSON.stringify(detail).replace(/(Bearer\s+|(?:api[_-]?key|password|secret|token)\s*[:=]\s*)\S+/gi, '$1***'));
    await page.screenshot({ path: join(home, 'native-failure.png') }).catch(() => {});
  }
  if (!exited) {
    const script = `Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes; $p=Get-Process -Id ${child.pid}; $root=[System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle); if($root){$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {$_.Current.Name}}`;
    const diagnostics = await new Promise(resolve => execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { windowsHide: true, timeout: 10_000 }, (_error, stdout) => resolve(stdout || 'No native dialog text.')));
    console.error(String(diagnostics).slice(0, 3000));
  }
  throw error;
} finally {
  if (page && !exited) await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('stop_backend_and_quit')).catch(() => {});
  for (let attempt = 0; !exited && attempt < 50; attempt++) await wait(100);
  if (!exited) await new Promise(resolve => execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, resolve));
  await browser?.close().catch(() => {});
}
