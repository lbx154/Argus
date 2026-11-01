// Visual/style acceptance using actual desktop source and an isolated web build.
// Only native IPC and CLI metadata are fixtures. No inference, installed app,
// user data, account configuration, package build, or release operation.
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createServer } from 'vite';
import { chromium } from 'playwright';
import { Store } from '../../packages/core/src/store.ts';
import { serve } from '../../packages/core/src/serve/index.ts';
import { priceBook } from '../../packages/core/src/agent/prices.ts';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
mkdirSync(join(root, '.preview-qa'), { recursive: true });
const evidence = mkdtempSync(join(root, '.preview-qa', 'warm-ui-'));
const store = new Store(join(evidence, 'home'));
const project = store.create({ workdir: '', request: '', backend: 'pi', model: null });
const cliNames = ['codex', 'claude', 'pi', 'dsh'];
for (const name of ['codex', 'claude', 'copilot', 'cursor', 'pi', 'opencode', 'grok', 'qoder', 'dsh']) {
  process.env[`ARGUS_CLI_${name.toUpperCase()}_BIN`] = cliNames.includes(name) ? process.execPath : join(evidence, 'not-present', name);
}
priceBook.refresh = async () => {}; // No network price fetching in visual fixtures.
let inferenceCalls = 0;
const call = Object.assign(async () => { inferenceCalls++; throw Error('Inference is forbidden in visual acceptance.'); }, {
  models: async backend => [{ model: `${backend}/visual-example`, name: '界面示例模型（非账号验证）', default: true, efforts: ['low', 'high'] }],
});
// Web and desktop have different Vite majors. Build web assets with its OWN CLI
// into a fresh QA directory; do not mix the Vite 8 React plugin into Vite 7.
const webAssets = join(evidence, 'web-assets');
console.log('VISUAL_PHASE=isolated-web-assets');
execFileSync(process.execPath, [join(root, 'frontend/web/node_modules/vite/bin/vite.js'), 'build', '--outDir', webAssets],
  { cwd: join(root, 'frontend/web'), stdio: 'inherit' });
console.log('VISUAL_PHASE=api-start');
const api = await serve({ store, callAgent: call, backend: 'pi', model: null, port: 0, web: webAssets });
console.log('VISUAL_PHASE=api-ready');
let shell, browser;
const origin = server => `http://127.0.0.1:${server.httpServer.address().port}`;
const screenshots = [];
try {
  console.log('VISUAL_PHASE=shell-create');
  // Only the desktop's Vite owns this dev server. The iframe is served by core.
  shell = await createServer({ configFile: false, root: join(root, 'desktop-tauri/src'), base: './', envDir: false,
    define: { __ARGUS_DESKTOP_BRAND__: 'null' },
    cacheDir: join(evidence, 'shell-cache'), server: { host: '127.0.0.1', port: 0, strictPort: false } });
  await shell.listen();
  console.log('VISUAL_PHASE=browser-start');
  browser = await chromium.launch({ headless: true, channel: process.env.ARGUS_BROWSER_CHANNEL || 'msedge' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', reducedMotion: 'reduce' });
  const cockpit = `${api.url}/?project=${project.id}&desktopTheme=light`;
  const bridge = `
    const appearance = { theme: 'light', resolvedTheme: 'light' };
    const fixture = window.__argusVisual = { firstRun: false };
    export const desktopBridge = new Proxy({
      getStatus: async () => ({ state: 'ready', message: '本地界面预览', url: ${JSON.stringify(cockpit)} }),
      getSetup: async () => ({ complete: !fixture.firstRun, port: 8799, runnerKind: 'pi', runnerConfigured: true,
        runnerBins: {}, detectedRunners: { pi: 'pi', dsh: 'dsh', claude: 'claude', codex: 'codex' },
        piConfiguration: { configDir: '' }, releaseIdentity: { packageVersion: '1.0.0', distribution: 'preview' }, runtimeIdentity: { state: 'ready' } }),
      getAppearance: async () => appearance,
      setAppearance: async ({ theme }) => Object.assign(appearance, { theme, resolvedTheme: theme === 'dark' ? 'dark' : 'light' }),
      setWindowTheme: async () => {},
      isWindowVisible: async () => true,
      getUpdateStatus: async () => ({ state: 'idle', currentVersion: '1.0.0-preview.2', userInitiated: false }),
      openCockpit: async () => ${JSON.stringify(cockpit)},
      stopBackend: async () => {},
    }, { get: (target, key) => target[key] || (String(key).startsWith('on') ? (() => () => {}) : (async () => undefined)) });
  `;
  await context.route('**/bridge.ts', route => route.fulfill({ contentType: 'application/javascript', body: bridge }));
  const page = await context.newPage();
  page.setDefaultTimeout(20_000); page.setDefaultNavigationTimeout(30_000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const capture = async name => {
    await page.screenshot({ path: join(evidence, name), animations: 'disabled' }); screenshots.push(name); console.log(`VISUAL_CAPTURE=${name}`);
  };
  console.log('VISUAL_PHASE=page-open');
  await page.goto(origin(shell));
  const frame = page.frameLocator('#cockpitFrame');
  await frame.locator('[data-runtime-open-models]').waitFor({ timeout: 60_000 });
  await page.locator('#splash').waitFor({ state: 'hidden', timeout: 30_000 });
  await frame.locator('.conversation-composer').waitFor();
  const warm = 'rgb(247, 243, 232)';
  const color = locator => locator.evaluate(element => getComputedStyle(element).backgroundColor);
  assert.equal(await color(page.locator('.desktop-menu-bar')), warm);
  for (const selector of ['body', '.workspace-header', '.chrome-seam-surface', '.map-composer']) {
    assert.equal(await color(frame.locator(selector).first()), warm, selector);
  }
  await capture('01-main-light.png');
  await frame.locator('[data-runtime-open-models]').click();
  await frame.locator('[data-role-model-picker]').waitFor();
  await frame.locator('[data-role-model="pi/visual-example"]').waitFor();
  assert.equal(await color(frame.locator('[role="dialog"]')), 'rgb(252, 250, 244)');
  assert.equal(await frame.locator('[data-role-cli]').evaluate(element => getComputedStyle(element).borderRadius), '6px');
  assert.equal(await frame.locator('[data-role-tab]').first().evaluate(element => getComputedStyle(element).borderRadius), '10px');
  await capture('02-role-picker-light.png');
  await frame.locator('[data-modal-close]').click();
  await frame.locator('.workspace-tabs').getByRole('button', { name: /^(工作台|Workbench)$/ }).click();
  await frame.locator('.integrated-workbench').waitFor({ timeout: 30_000 });
  await capture('03-workbench-light.png');
  await frame.locator('.workspace-tabs').getByRole('button', { name: /^(地图|Map)$/ }).click();
  await frame.locator('.argus-map').waitFor({ timeout: 30_000 });
  await capture('04-map-light.png');
  await frame.locator('.workspace-tabs').getByRole('button', { name: /^(对话|Activity|Conversation)$/ }).click();
  await page.locator('#fileMenuTrigger').click();
  await page.locator('[data-menu-action="settings"]').click();
  await page.locator('#wizard').waitFor();
  await capture('05-desktop-settings.png');
  await page.locator('#wizardCancel').click();
  await page.locator('#wizard').waitFor({ state: 'hidden' });
  await frame.locator('.sidebar-more > summary').click();
  await frame.getByRole('button', { name: /主题；切换|theme; switch/i }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark'
    && getComputedStyle(document.querySelector('.desktop-menu-bar')).backgroundColor === 'rgb(21, 21, 23)');
  await frame.locator('.sidebar-more > summary').click();
  assert.equal(await color(page.locator('.desktop-menu-bar')), 'rgb(21, 21, 23)');
  assert.equal(await color(frame.locator('.workspace-header')), 'rgb(21, 21, 23)');
  await capture('06-main-dark.png');
  await frame.locator('[data-runtime-open-models]').click();
  await frame.locator('[data-role-model-picker]').waitFor();
  await capture('07-role-picker-dark.png');
  await page.setViewportSize({ width: 480, height: 900 });
  assert.equal(await frame.locator('html').evaluate(el => el.scrollWidth <= window.innerWidth), true);
  await capture('08-role-picker-narrow.png');
  assert.equal(inferenceCalls, 0); assert.deepEqual(errors, []);
  writeFileSync(join(evidence, 'result.json'), JSON.stringify({ ok: true, mode: 'browser-source-visual-fixture',
    nativeIpc: 'simulated', modelCatalog: 'synthetic', inferenceCalls, packageBuilt: false, screenshots }, null, 2), { flag: 'wx' });
  console.log(`WARM_UI_ACCEPTANCE=${evidence}`);
} catch (error) {
  console.error(`Visual evidence retained: ${evidence}`);
  throw error;
} finally { await browser?.close(); await shell?.close(); await api.close(); }
