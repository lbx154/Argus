import { expect, test } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { resolve, delimiter } from 'node:path';
import { createInterface } from 'node:readline';

let server: ChildProcess;
let origin: string;
let serverErrors = "";

test.beforeAll(async () => {
  const root = resolve('..');
  server = spawn(process.env.ARGUS_BUILD_PYTHON || 'python', ['desktop-tauri/tests/workbench-layout-server.py'], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: [root, process.env.PYTHONPATH].filter(Boolean).join(delimiter) },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stderr!.on('data', (chunk) => { serverErrors += chunk.toString(); });
  origin = await new Promise<string>((resolveOrigin, reject) => {
    const timer = setTimeout(() => reject(new Error(`Layout fixture timed out: ${serverErrors}`)), 20_000);
    const lines = createInterface({ input: server.stdout! });
    lines.on('line', (line) => {
      if (!line.startsWith('{"origin":')) return;
      clearTimeout(timer);
      resolveOrigin(JSON.parse(line).origin);
      lines.close();
    });
    server.once('error', (error) => { clearTimeout(timer); reject(error); });
    server.once('exit', (code) => { clearTimeout(timer); reject(new Error(`Layout fixture exited ${code}: ${serverErrors}`)); });
  });
  await expect.poll(async () => {
    try {
      return (await fetch(`${origin}/api/meta`, { headers: { Authorization: 'Bearer local-layout-test' } })).status;
    } catch { return 0; }
  }).toBe(200);
});

test.afterEach(async ({ page }, info) => {
  await page.unrouteAll({ behavior: 'wait' });
  if (info.status === info.expectedStatus) return;
  console.error('Workbench fixture errors:', serverErrors);
  for (const document of page.frames()) {
    console.error('Rendered frame:', document.url(), await document.locator('body').innerText());
  }
});

test.afterAll(() => { server?.kill(); });

test('titles expand with the pane and trial model/Key controls remain visible in both themes', async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  await page.route('**/bridge.ts', (route) => route.fulfill({
    contentType: 'application/javascript',
    body: `export const desktopBridge = new Proxy({
      getStatus: async () => ({ state: 'ready', message: 'Ready' }),
      getSetup: async () => ({ complete: true, trialMode: true, host: '127.0.0.1', port: 8799,
        runnerKind: 'copilot', runnerConfigured: true, runnerBins: {}, detectedRunners: {},
        piConfiguration: { configDir: '' }, releaseIdentity: {}, runtimeIdentity: {} }),
      getAppearance: async () => ({ theme: 'light', resolvedTheme: 'light' }),
      getUpdateStatus: async () => ({ state: 'idle', currentVersion: '0.1.5', userInitiated: false }),
      openCockpit: async () => ${JSON.stringify(`${origin}/?token=local-layout-test&project=s-layout&view=activity`)}
    }, { get: (object, key) => object[key] || (() => undefined) });`,
  }));
  const configUrl = `${origin}/api/projects/s-layout/config`;
  // This case exercises trial layout and native Key controls. Keep its trial
  // configuration deterministic instead of waiting for backend discovery on
  // a cold Windows runner before applying the existing trial-mode override.
  await page.route(configUrl, (route) => route.fulfill({ json: {
    schema_version: 1, generated_at_utc: '2026-09-11T00:00:00Z', trial_mode: true,
    roles: [{
      role: 'manager', backend: 'copilot', backend_label: 'Copilot', backend_source: 'trial fixture',
      model: 'gpt-5.5', model_source: 'trial fixture', reasoning_effort: 'high',
      reasoning_effort_source: 'trial fixture', description: 'Layout fixture manager',
    }],
    operator_knobs: [], how_to_change: [],
  } }));
  await page.setViewportSize({ width: 1280, height: 820 });
  const [configResponse] = await Promise.all([
    page.waitForResponse(configUrl),
    page.goto('/'),
  ]);
  expect(configResponse.ok()).toBe(true);
  expect(await configResponse.json()).toMatchObject({ trial_mode: true });
  const frame = page.frameLocator('#cockpitFrame');
  const title = frame.locator('.topbar-title').filter({ visible: true });
  await expect(title).toBeVisible({ timeout: 20_000 });
  const narrow = await title.boundingBox();
  const divider = await frame.getByRole('separator', { name: /preview|预览/i }).boundingBox();
  await page.mouse.move(divider!.x + divider!.width / 2, divider!.y + 100);
  await page.mouse.down();
  await page.mouse.move(divider!.x + 120, divider!.y + 100, { steps: 6 });
  await page.mouse.up();
  await expect.poll(async () => (await title.boundingBox())!.width).toBeGreaterThan(narrow!.width + 70);
  await page.setViewportSize({ width: 1920, height: 820 });
  await expect.poll(async () => (await title.boundingBox())!.width).toBeGreaterThan(narrow!.width + 250);
  await expect.poll(() => title.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  const runtime = frame.locator('.composer-runtime').filter({ visible: true });
  const trialModel = /^(Trial|试用) · GPT-5\.5 · high$/;
  await expect(runtime.locator('.composer-runtime-model')).toHaveText(trialModel);
  const themeButton = frame.getByRole('button', { name: /theme; switch|主题；切换/ });
  for (const theme of ['light', 'dark']) {
    if (await frame.locator('html').getAttribute('data-theme') !== theme) await themeButton.click();
    await expect(frame.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    const button = await themeButton.boundingBox();
    const icon = await themeButton.locator('svg').boundingBox();
    expect(Math.abs(icon!.x + icon!.width / 2 - button!.x - button!.width / 2)).toBeLessThan(1);
    expect(Math.abs(icon!.y + icon!.height / 2 - button!.y - button!.height / 2)).toBeLessThan(1);
    await page.screenshot({ path: testInfo.outputPath(`runtime-${theme}.png`) });
  }
  await runtime.getByRole('button', { name: /更换 Key|Change Key/ }).click();
  await expect(page.locator('#trialKey')).toBeVisible();
  await page.keyboard.press('Escape');
  await frame.getByRole('button', { name: /打开设置|Open settings/ }).click();
  await frame.getByRole('dialog').getByRole('button', { name: /更换 Key|Change Key/, exact: true }).click();
  await expect(page.locator('#trialKey')).toBeVisible();
  await page.keyboard.press('Escape');
  await frame.locator('.workspace-tabs').getByRole('button', { name: /^(Map|地图)$/ }).click();
  await expect(frame.locator('.map-island-dock .composer-runtime-model')).toHaveText(trialModel);
  await expect(frame.locator('.map-island-dock .composer-runtime button')).toBeInViewport();
});

test('real embedded workbench retains typography and fits the pane between both sidebars', async ({ page }) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('response', (response) => {
    if (response.status() >= 400) console.error('HTTP failure:', response.status(), response.url());
  });
  page.on('requestfailed', (request) => console.error('Request failed:', request.url(), request.failure()));
  // Only native IPC is simulated. The iframe loads the actual production web
  // bundle, project API and styles; a placeholder cockpit cannot test layout.
  await page.route('**/bridge.ts', (route) => route.fulfill({
    contentType: 'application/javascript',
    body: `export const desktopBridge = new Proxy({
      getStatus: async () => ({ state: 'ready', message: 'Ready' }),
      getSetup: async () => ({ complete: true, trialMode: true, host: '127.0.0.1', port: 8799,
        runnerKind: 'copilot', runnerConfigured: true, runnerBins: {}, detectedRunners: {},
        piConfiguration: { configDir: '' }, releaseIdentity: {}, runtimeIdentity: {} }),
      getAppearance: async () => ({ theme: 'light', resolvedTheme: 'light' }),
      getUpdateStatus: async () => ({ state: 'idle', currentVersion: '0.1.3', userInitiated: false }),
      openCockpit: async () => ${JSON.stringify(`${origin}/?token=local-layout-test&project=s-layout&view=activity`)}
    }, { get: (object, key) => object[key] || (() => undefined) });`,
  }));
  await page.goto('/');
  const frame = page.frameLocator('#cockpitFrame');
  const composer = frame.locator('.conversation-composer');
  try {
    // Windows CI needs time for the first real project snapshot on a cold backend.
    await expect(composer).toBeVisible({ timeout: 20_000 });
  } catch (error) {
    console.error('Workbench startup errors:', errors, consoleErrors);
    for (const document of page.frames()) {
      console.error('Rendered frame:', document.url(), await document.locator('body').innerText());
    }
    throw error;
  }
  const composerStyle = () => composer.evaluate((el) => {
    const style = getComputedStyle(el);
    return { width: el.clientWidth, fontSize: style.fontSize, padding: style.padding };
  });
  const before = await composerStyle();
  const workspaceTabs = frame.locator('.workspace-tabs');
  const workbenchTab = workspaceTabs.getByRole('button', { name: /^(Workbench|工作台)$/ });
  const activityTab = workspaceTabs.getByRole('button', { name: /^(Activity|动态)$/ });
  const overviewTab = frame.getByRole('navigation', { name: /^(Workbench modules|工作台模块)$/ })
    .getByRole('button', { name: /^(Project overview|项目概览)$/ });
  await workbenchTab.click();
  // Workbench opens on Execution. Select the overview whose typography and
  // workspace modules this test verifies, independently of module order.
  await overviewTab.click();
  // The full workbench snapshot loads separately from the conversation snapshot.
  await expect(frame.locator('.overview-hero__copy h1')).toBeVisible({ timeout: 20_000 });
  // Loading the lazy workbench stylesheet must not restyle the conversation.
  // Compare before resizing, since the shell deliberately fits its sidebars
  // when the desktop window gets narrower.
  await activityTab.click();
  await expect(composer).toBeVisible();
  await expect.poll(composerStyle).toEqual(before);
  await workbenchTab.click();
  await overviewTab.click();

  for (const width of [1440, 1280, 1100, 1024, 960]) {
    await page.setViewportSize({ width, height: 820 });
    await expect.poll(() => frame.locator('.integrated-workbench').evaluate((pane) => {
      const copy = pane.querySelector('.overview-hero__copy')!;
      const heading = copy.querySelector('h1')!;
      const modules = pane.querySelector('.module-grid')!;
      const content = pane.querySelector('.ros-content')!;
      const style = getComputedStyle(heading);
      return {
        readableTitle: parseFloat(style.fontSize) > parseFloat(getComputedStyle(copy).fontSize),
        headingSpacing: parseFloat(style.marginBottom) > 0,
        fits: copy.scrollWidth <= copy.clientWidth + 1 && content.scrollWidth <= content.clientWidth + 1,
        stacked: modules.getBoundingClientRect().top >= copy.getBoundingClientRect().bottom,
      };
    })).toEqual({ readableTitle: true, headingSpacing: true, fits: true, stacked: true });
  }

  await page.setViewportSize({ width: 1280, height: 820 });
  for (const [module, selector] of [['experiments', '.experiment-v3-grid'], ['ide', '.vscode-shell']]) {
    await frame.locator(`[data-module="${module}"]`).click();
    await expect(frame.locator(selector)).toBeVisible();
    await expect.poll(() => frame.locator('.ros-content[aria-hidden="false"]').evaluate((content) => (
      content.scrollWidth <= content.clientWidth + 1
    ))).toBe(true);
    if (module === 'experiments') {
      // Inject the server's HTTP 200 failure contract, without starting an agent.
      await page.route(`${origin}/api/projects/s-layout/daemon/start`, (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ rc: 2, command_status: 'failed', error: 'Executor startup diagnostic' }),
      }));
      await frame.locator('.experiment-header-actions').getByRole('button', { name: /继续运行|Resume/ }).click();
      await expect(frame.locator('.inline-error')).toContainText('Executor startup diagnostic');
    }
  }
  await activityTab.click();
  await expect(composer).toBeVisible();
  expect(errors).toEqual([]);
});
