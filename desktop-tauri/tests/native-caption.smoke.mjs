// Real Windows DWM + Tauri verification. A development build uses a different
// single-instance identity from the user's running Preview. ALL data is isolated.
import assert from 'node:assert/strict';
import { spawn, execFile, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { copyVerified } from '../scripts/verified-copy.mjs';

assert.equal(process.platform, 'win32');
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..'), root = resolve(desktop, '..');
// Never take over, close or reconfigure an existing stable instance.
execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
  '$p=@(Get-Process -Name Argus -ErrorAction SilentlyContinue | Where-Object { $_.Path -notlike "*preview*" }); if($p.Count){throw "A non-preview Argus instance is running; close it before this isolated test."}'], { stdio: 'pipe' });
const home = fs.mkdtempSync(join(root, '.preview-qa', 'native-caption-'));
const executable = join(home, 'application', 'Argus.exe');
const configRoot = join(desktop, 'src-tauri');
const config = name => JSON.parse(fs.readFileSync(join(configRoot, name), 'utf8'));
const resources = { ...config('tauri.conf.json').bundle.resources, ...config('tauri.windows.conf.json').bundle.resources };
const copy = (from, to) => {
  const stat = fs.lstatSync(from);
  assert.equal(stat.isSymbolicLink(), false);
  if (stat.isDirectory()) { fs.mkdirSync(to, { recursive: true }); for (const name of fs.readdirSync(from)) copy(join(from, name), join(to, name)); }
  else { fs.mkdirSync(dirname(to), { recursive: true }); copyVerified(from, to); }
};
copy(join(configRoot, 'target/debug/Argus.exe'), executable);
for (const [source, relative] of Object.entries(resources)) copy(resolve(configRoot, source), join(home, 'application', relative));
const portServer = createServer();
await new Promise(resolve => portServer.listen(0, '127.0.0.1', resolve));
const debugPort = portServer.address().port;
await new Promise(resolve => portServer.close(resolve));
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const child = spawn(executable, [], { cwd: home, env: { ...process.env,
  APPDATA: join(home, 'roaming'), LOCALAPPDATA: join(home, 'local'), ARGUS_HOME: join(home, 'argus-home'),
  WEBVIEW2_USER_DATA_FOLDER: join(home, 'webview'), WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort}`,
}, stdio: ['ignore', 'ignore', 'ignore'] });
let exited = false; child.once('exit', () => { exited = true; });
let browser, page;
try {
  for (let attempt = 0; ; attempt++) {
    try { if ((await fetch(`http://127.0.0.1:${debugPort}/json/version`)).ok) break; } catch {}
    assert.ok(!exited && attempt < 150, 'Test WebView2 did not start'); await wait(100);
  }
  browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`);
  for (let attempt = 0; !page; attempt++) {
    page = browser.contexts().flatMap(c => c.pages()).find(p => !p.isClosed() && /tauri[.:]/.test(p.url()));
    assert.ok(attempt < 150 && !exited, 'Test window unavailable'); if (!page) await wait(100);
  }
  page.setDefaultTimeout(30_000);
  await page.locator('#wizard').waitFor({ state: 'visible' });
  const setup = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('get_setup'));
  assert.equal(setup.releaseIdentity.distribution, 'development', 'Native test must not enable release updates.');
  const inspect = async (operation, screenshot) => {
    const args = ['-NoProfile', '-NonInteractive', '-File', join(desktop, 'tests/native-caption-inspect.ps1'),
      '-TargetProcessId', String(child.pid), '-ExpectedExecutable', executable, '-Operation', operation];
    if (screenshot) args.push('-Screenshot', join(home, screenshot));
    const stdout = await new Promise((resolve, reject) => execFile('powershell.exe', args, { windowsHide: true, timeout: 15_000 },
      (error, stdout, stderr) => {
        if (!error) return resolve(stdout);
        const detail = String(stderr).slice(-4000).replace(/(Bearer\s+|(?:password|secret|token)\s*[:=]\s*)\S+/gi, '$1***');
        reject(new Error(`Native DWM inspection failed for the test-owned window: ${detail}`));
      }));
    return JSON.parse(String(stdout).trim());
  };
  const results = {};
  for (const [theme, expected, text] of [['light', '#f7f3e8', '#0e0e10'], ['dark', '#151517', '#f2f2f0']]) {
    await page.evaluate(theme => window.__TAURI_INTERNALS__.invoke('set_appearance', { input: { theme } }), theme);
    await page.reload(); await page.locator('#wizard').waitFor({ state: 'visible' });
    await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme);
    const reading = await inspect('restore', `whole-window-${theme}.png`);
    assert.equal(reading.caption, expected, 'Native caption pixels must match the shell, not white/grey.');
    if (reading.border !== null) assert.equal(reading.border, expected);
    if (reading.text !== null) assert.equal(reading.text, text);
    if (reading.immersiveDark !== null) assert.equal(reading.immersiveDark, theme === 'dark' ? 1 : 0);
    for (const flag of ['nativeCaption', 'minimizeButton', 'maximizeButton', 'systemMenu']) assert.equal(reading[flag], true, flag);
    results[theme] = reading;
    const maximized = await inspect('maximize'); assert.equal(maximized.maximized, true); assert.equal(maximized.caption, expected);
    const minimized = await inspect('minimize'); assert.equal(minimized.minimized, true);
    // Restoring a minimised maximised window preserves its previous maximised state.
    const restored = await inspect('restore'); assert.equal(restored.minimized, false); assert.equal(restored.maximized, true); assert.equal(restored.caption, expected);
    const normal = await inspect('restore'); assert.equal(normal.minimized, false); assert.equal(normal.maximized, false); assert.equal(normal.caption, expected);
  }
  fs.writeFileSync(join(home, 'result.json'), JSON.stringify({ ok: true, mode: 'real-native-development-window',
    isolatedData: true, inferenceCalls: 0, releaseUpdatesEnabled: false, results }, null, 2), { flag: 'wx' });
  console.log(`NATIVE_CAPTION_ACCEPTANCE=${home}`);
} finally {
  if (page && !exited) await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('stop_backend_and_quit')).catch(() => {});
  for (let attempt = 0; !exited && attempt < 50; attempt++) await wait(100);
  if (!exited) await new Promise(resolve => execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, resolve));
  await browser?.close().catch(() => {});
}
