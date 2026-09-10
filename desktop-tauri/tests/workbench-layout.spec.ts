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
  if (info.status === info.expectedStatus) return;
  console.error('Workbench fixture errors:', serverErrors);
  for (const document of page.frames()) {
    console.error('Rendered frame:', document.url(), await document.locator('body').innerText());
  }
});

test.afterAll(() => { server?.kill(); });

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
  await frame.locator('.workspace-tab').nth(2).click();
  // The full workbench snapshot loads separately from the conversation snapshot.
  await expect(frame.locator('.overview-hero__copy h1')).toBeVisible({ timeout: 20_000 });
  // Loading the lazy workbench stylesheet must not restyle the conversation.
  // Compare before resizing, since the shell deliberately fits its sidebars
  // when the desktop window gets narrower.
  await frame.locator('.workspace-tab').nth(1).click();
  await expect(composer).toBeVisible();
  await expect.poll(composerStyle).toEqual(before);
  await frame.locator('.workspace-tab').nth(2).click();

  for (const width of [1440, 1280, 1100, 1024, 960]) {
    await page.setViewportSize({ width, height: 820 });
    await expect.poll(() => frame.locator('.integrated-workbench').evaluate((pane) => {
      const copy = pane.querySelector('.overview-hero__copy')!;
      const heading = copy.querySelector('h1')!;
      const stats = pane.querySelector('.overview-hero__stats')!;
      const content = pane.querySelector('.ros-content')!;
      const style = getComputedStyle(heading);
      return {
        readableTitle: parseFloat(style.fontSize) > parseFloat(getComputedStyle(copy).fontSize),
        headingSpacing: parseFloat(style.marginTop) > 0,
        fits: copy.scrollWidth <= copy.clientWidth + 1 && content.scrollWidth <= content.clientWidth + 1,
        stacked: stats.getBoundingClientRect().top >= copy.getBoundingClientRect().bottom,
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
  await frame.locator('.workspace-tab').nth(1).click();
  await expect(composer).toBeVisible();
  expect(errors).toEqual([]);
});
