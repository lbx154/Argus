import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

async function launch(page: Page, complete = false) {
  await page.route('**/bridge.ts', (route) => route.fulfill({
    contentType: 'application/javascript',
    body: 'export const desktopBridge = window.desktopTest.bridge;',
  }));
  await page.route('http://127.0.0.1:18880/**', (route) => route.fulfill({
    contentType: 'text/html',
    body: '<!doctype html><title>Test cockpit</title><input id="draft" aria-label="Draft"><p>Manager · Engineer</p>',
  }));
  await page.addInitScript((configured) => {
    const callbacks: Record<string, (value: unknown) => void> = {};
    const state = {
      status: { state: configured ? 'ready' : 'idle', message: configured ? '已就绪' : '请选择 Agent CLI' },
      complete: configured,
      failure: '',
      saveCount: 0,
      saved: null as unknown,
      trialMode: false,
      trialCount: 0,
      delaySave: false,
      url: 'http://127.0.0.1:18880/',
      savedAppearance: null as unknown,
      releaseSave: null as (() => void) | null,
      emitDownload: (downloaded_bytes: number, total_bytes: number | null) =>
        callbacks.download?.({ downloaded_bytes, total_bytes }),
      emitTrialProgress: (message: string) => callbacks.trial?.(message),
      emit: (value: { state: string; message: string }) => {
        state.status = value;
        callbacks.status?.(value);
      },
      bridge: {} as Record<string, unknown>,
    };
    state.bridge = {
      getStatus: async () => state.status,
      getAppearance: async () => ({ theme: 'system', resolvedTheme: 'light' }),
      setWindowTheme: async () => undefined,
      setAppearance: async (appearance: unknown) => { state.savedAppearance = appearance; return appearance; },
      getSetup: async () => ({
        complete: state.complete, trialMode: state.trialMode, host: '127.0.0.1', port: 18880,
        runnerKind: 'codex', runnerConfigured: state.complete,
        runnerBins: {}, detectedRunners: { codex: 'C:/agents/codex.cmd', pi: 'C:/agents/pi.cmd' },
        piConfiguration: { configDir: '' },
        releaseIdentity: { packageVersion: '0.1.1', releaseId: 'test', sourceDigest: 'test', distribution: 'preview' },
        runtimeIdentity: { state: state.status.state },
      }),
      openCockpit: async () => state.url,
      completeSetup: async (input: unknown) => {
        state.saveCount++;
        state.saved = input;
        if (state.delaySave) await new Promise<void>((resolve) => { state.releaseSave = resolve; });
        if (state.failure) return { ok: false, error: state.failure };
        state.complete = true;
        state.emit({ state: 'ready', message: '已就绪' });
        return { ok: true };
      },
      completeTrialSetup: async (_key: string) => {
        state.trialCount++;
        callbacks.trial?.('正在准备试用环境…');
        if (state.delaySave) await new Promise<void>((resolve) => { state.releaseSave = resolve; });
        if (state.failure) return { ok: false, error: state.failure };
        state.complete = state.trialMode = true;
        state.emit({ state: 'ready', message: '已就绪' });
        return { ok: true };
      },
      restartBackend: async () => state.emit({ state: 'ready', message: '已就绪' }),
      chooseRunner: async () => 'C:/custom/codex.cmd',
      getUpdateStatus: async () => ({ state: 'idle', currentVersion: '0.1.1', userInitiated: false }),
      checkForUpdate: async () => ({ state: 'idle', currentVersion: '0.1.1', userInitiated: true, detail: '预览构建不安装发布更新。' }),
      dismissUpdate: async () => undefined,
      onTrialProgress: (callback: (value: unknown) => void) => { callbacks.trial = callback; },
      onTrialDownload: (callback: (value: unknown) => void) => { callbacks.download = callback; },
      onStatus: (callback: (value: unknown) => void) => { callbacks.status = callback; },
      onUpdateStatus: () => undefined,
      onShowSetup: () => undefined,
      onNewChat: () => undefined,
      onOpenDelivery: () => undefined,
    };
    (window as unknown as { desktopTest: typeof state }).desktopTest = state;
  }, complete);
  await page.goto('/');
}

async function finishSteps(page: Page) {
  await page.locator('#wizardNext').click();
  await page.locator('#wizardNext').click();
}

async function openSettings(page: Page) {
  await page.locator('#fileMenuTrigger').click();
  await page.locator('[data-menu-action="settings"]').click();
  await expect(page.locator('#wizard')).toBeVisible();
}

// Only the test harness owns this object; production ships no test IPC or globals.
async function configure(page: Page, changes: Record<string, unknown>) {
  await page.evaluate((values) => Object.assign((window as any).desktopTest, values), changes);
}

test('first launch requires an explicit CLI selection and saves the selected port', async ({ page }) => {
  await launch(page);
  await expect(page.locator('#wizard')).toBeVisible();
  await expect(page.locator('#cockpit')).toBeHidden();
  await expect(page.locator('#wizardNext')).toBeDisabled();
  await page.locator('[data-kind="pi"]').click();
  await page.locator('#wizardNext').click();
  await page.locator('#portInput').fill('18901');
  await expect(page.locator('[data-appearance]')).toHaveCount(0);
  await page.locator('#wizardNext').click();
  await expect(page.locator('#summaryUrl')).toHaveText('127.0.0.1:18901');
  await page.locator('#wizardFinish').click();
  await expect(page.locator('#wizard')).toBeHidden();
  await expect(page.locator('#cockpit')).toBeVisible();
  await expect(page.locator('#splash')).toBeHidden();
  const saved = await page.evaluate(() => (window as any).desktopTest.saved);
  expect(saved).toMatchObject({ port: 18901, runnerKind: 'pi' });
});

test('prototype hardening applies only to the trusted shell, not the drawing iframe', async ({ page }) => {
  await page.addInitScript(readFileSync(new URL('../src-tauri/src/shell-init.js', import.meta.url), 'utf8'));
  await launch(page, true);
  await expect(page.locator('#cockpit')).toBeVisible();
  expect(await page.evaluate(() => Object.isFrozen(Object.prototype))).toBe(true);
  // The shell becomes visible before WebKit commits the iframe navigation.
  const draft = page.frameLocator('#cockpitFrame').locator('#draft');
  expect(await draft.evaluate(() => {
    const prototype = Object.create(Object.prototype);
    prototype.constructor = function GraphNode() {};
    return Object.isFrozen(Object.prototype);
  })).toBe(false);
});

test('returning user enters the cockpit without repeating setup', async ({ page }) => {
  await launch(page, true);
  await expect(page.locator('#cockpit')).toBeVisible();
  await expect(page.locator('#wizard')).toBeHidden();
  await expect(page.locator('#splash')).toBeHidden();
  await expect(page.locator('#desktopContext')).toContainText('Preview');
});

test('invalid port is explained before saving', async ({ page }) => {
  await launch(page);
  await page.locator('[data-kind="codex"]').click();
  await page.locator('#wizardNext').click();
  await page.locator('#portInput').fill('65536');
  await page.locator('#wizardNext').click();
  await expect(page.locator('#portError')).toBeVisible();
  await expect(page.locator('#portInput')).toBeFocused();
  expect(await page.evaluate(() => (window as any).desktopTest.saveCount)).toBe(0);
});

test('save failure stays visible and preserves both settings and the cockpit draft', async ({ page }) => {
  await launch(page, true);
  await page.frameLocator('#cockpitFrame').locator('#draft').fill('unsent research question');
  await openSettings(page);
  await page.locator('[data-kind="pi"]').click();
  await finishSteps(page);
  await configure(page, { failure: '端口已被占用，现有设置未更改。' });
  await page.locator('#wizardFinish').click();
  await expect(page.locator('#wizardError')).toBeVisible();
  await expect(page.locator('#wizardError')).toContainText('端口已被占用');
  await expect(page.locator('#summaryRunner')).toContainText('Pi');
  await page.locator('#wizardCancel').click();
  await expect(page.frameLocator('#cockpitFrame').locator('#draft')).toHaveValue('unsent research question');
});

test('a pending save cannot be double-submitted or cancelled', async ({ page }) => {
  await launch(page, true);
  await openSettings(page);
  await finishSteps(page);
  await configure(page, { delaySave: true });
  await page.locator('#wizardFinish').click();
  await expect(page.locator('#wizardFinish')).toBeDisabled();
  await expect(page.locator('#wizardCancel')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.locator('#wizard')).toBeVisible();
  expect(await page.evaluate(() => (window as any).desktopTest.saveCount)).toBe(1);
  await page.evaluate(() => (window as any).desktopTest.releaseSave());
  await expect(page.locator('#wizard')).toBeHidden();
});

test('recovery at the same URL retains the React document and unsent draft', async ({ page }) => {
  await launch(page, true);
  await page.frameLocator('#cockpitFrame').locator('#draft').fill('preserve me');
  await page.evaluate(() => (window as any).desktopTest.emit({ state: 'error', message: '暂时无法连接' }));
  await expect(page.locator('#retry')).toBeVisible();
  await page.locator('#retry').click();
  await expect(page.frameLocator('#cockpitFrame').locator('#draft')).toHaveValue('preserve me');
  await expect(page.locator('#splash')).toBeHidden();
});

test('only the authenticated cockpit can request desktop settings', async ({ page }) => {
  await launch(page, true);
  await expect(page.locator('#cockpit')).toBeVisible();
  await page.evaluate(() => window.postMessage({ type: 'argus:show-setup' }, '*'));
  await expect(page.locator('#wizard')).toBeHidden();
  const cockpit = page.frames().find((frame) => frame.url().startsWith('http://127.0.0.1:18880/'))!;
  await cockpit.evaluate(() => window.parent.postMessage({ type: 'argus:show-setup' }, '*'));
  await expect(page.locator('#wizard')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#wizard')).toBeHidden();
});

test('runtime file warnings keep the working document and draft accessible', async ({ page }) => {
  await launch(page, true);
  await page.frameLocator('#cockpitFrame').locator('#draft').fill('keep this work');
  await page.evaluate(() => (window as any).desktopTest.emit({ state: 'ready', message: '已就绪', warning: '运行包文件暂时无法读取' }));
  await expect(page.locator('#runtimeNotice')).toBeVisible();
  await expect(page.locator('#cockpit')).toBeVisible();
  await expect(page.frameLocator('#cockpitFrame').locator('#draft')).toHaveValue('keep this work');
  await page.evaluate(() => (window as any).desktopTest.emit({ state: 'ready', message: '已就绪' }));
  await expect(page.locator('#runtimeNotice')).toBeHidden();
});

test('a theme-only URL change does not remount the conversation', async ({ page }) => {
  await launch(page, true);
  await page.frameLocator('#cockpitFrame').locator('#draft').fill('unsent after theme switch');
  await configure(page, { url: 'http://127.0.0.1:18880/?desktopTheme=dark' });
  await openSettings(page);
  await expect(page.locator('[data-appearance]')).toHaveCount(0);
  await finishSteps(page);
  await page.locator('#wizardFinish').click();
  await expect(page.locator('#wizard')).toBeHidden();
  await expect(page.frameLocator('#cockpitFrame').locator('#draft')).toHaveValue('unsent after theme switch');
});

test('manual update check explains why preview does not install releases', async ({ page }) => {
  await launch(page, true);
  await page.locator('#helpMenuTrigger').click();
  await page.locator('[data-menu-action="check-update"]').click();
  await expect(page.locator('#updateNotice')).toBeVisible();
  await expect(page.locator('#updateTitle')).toHaveText('发布更新已禁用');
  await expect(page.locator('#updateInstall')).toBeHidden();
});

test('CLI grid and settings stay usable at the minimum supported window size', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 640 });
  await launch(page);
  for (const kind of ['codex', 'claude', 'copilot', 'cursor', 'pi', 'opencode', 'grok', 'qoder', 'dsh']) {
    await expect(page.locator(`[data-kind="${kind}"]`)).toBeInViewport();
  }
  await page.locator('[data-kind="pi"]').click();
  await expect(page.locator('#wizardNext')).toBeInViewport();
  await finishSteps(page);
  await expect(page.locator('#wizardFinish')).toBeInViewport();
});

for (const viewport of [{ width: 1280, height: 820 }, { width: 960, height: 640 }]) {
  test(`reopened settings keep the trial card and CLI controls in their columns at ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await launch(page, true);
    const draft = page.frameLocator('#cockpitFrame').locator('#draft');
    await draft.fill('Keep my unsent message');
    await openSettings(page);
    await expect(page.locator('.trial-invite')).toBeVisible();
    await expect.poll(async () => {
      const panel = await page.locator('.panel[data-panel="env"]').boundingBox();
      const invite = await page.locator('.trial-invite').boundingBox();
      return panel && invite ? invite.width / panel.width : 0;
    }).toBeGreaterThan(0.95);
    const intro = await page.locator('.panel[data-panel="env"] > .panel-intro').boundingBox();
    const controls = await page.locator('.panel-content-env').boundingBox();
    expect(controls!.x).toBeGreaterThan(intro!.x);
    expect(controls!.width).toBeGreaterThan(intro!.width);
    await expect(page.locator('#wizardNext')).toBeInViewport();
    await expect(page.locator('#wizardCancel')).toBeInViewport();
    await page.locator('#trialOpen').click();
    await expect(page.locator('#trialKey')).toBeInViewport();
    await expect(page.locator('#trialSubmit')).toBeInViewport();
    await page.keyboard.press('Escape');
    await expect(draft).toHaveValue('Keep my unsent message');
  });
}

test('internal trial key validates, retries privately, and opens the cockpit without account setup', async ({ page }) => {
  await launch(page);
  await page.locator('#trialOpen').click();
  await expect(page.getByText('如果您是拿到了内部测试的 Key，可以直接在这个地方填入使用')).toBeVisible();
  await expect(page.locator('#stepper')).toBeHidden();
  await page.locator('#trialKey').fill('invalid');
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#trialProgress')).toHaveText('请输入完整的内部测试 Key。');
  expect(await page.evaluate(() => (window as any).desktopTest.trialCount)).toBe(0);
  await configure(page, { failure: '此 Key 无效，请重试。' });
  await page.locator('#trialKey').fill('argus_trial_' + 'a'.repeat(64));
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#trialProgress')).toHaveText('此 Key 无效，请重试。');
  await expect(page.locator('#trialKey')).toHaveValue('');
  await configure(page, { failure: '', delaySave: true });
  await page.locator('#trialKey').fill('argus_trial_' + 'a'.repeat(64));
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#trialKey')).toHaveValue('');
  await expect(page.locator('#trialSubmit')).toBeDisabled();
  await expect(page.locator('#trialProgress')).toHaveText('正在准备试用环境…');
  await page.evaluate(() => (window as any).desktopTest.releaseSave());
  await expect(page.locator('#wizard')).toBeHidden();
  await expect(page.locator('#cockpit')).toBeVisible();
  expect(await page.evaluate(() => (window as any).desktopTest.trialMode)).toBe(true);
});

test('trial download shows real progress, stalled network advice and clears it on recovery', async ({ page }) => {
  await page.clock.install();
  await launch(page);
  await configure(page, { delaySave: true });
  await page.locator('#trialOpen').click();
  await page.locator('#trialKey').fill('argus_trial_' + 'a'.repeat(64));
  await page.locator('#trialSubmit').click();
  await page.evaluate(() => (window as any).desktopTest.emitDownload(0, null));
  await expect(page.locator('#trialDownloadBar')).toBeVisible();
  await expect(page.locator('#trialDownloadBar')).not.toHaveAttribute('value');
  await page.clock.fastForward(30_000);
  await expect(page.locator('#trialNetworkHint')).toBeVisible();
  await expect(page.locator('#trialNetworkHint')).toContainText('代理（梯子）');
  await page.evaluate(() => (window as any).desktopTest.emitDownload(1024 * 1024, 4 * 1024 * 1024));
  await expect(page.locator('#trialDownloadBar')).toHaveAttribute('value', '25');
  await expect(page.locator('#trialDownloadDetails')).toHaveText('25% · 1.0 MB / 4.0 MB');
  await expect(page.locator('#trialNetworkHint')).toBeHidden();
  await page.evaluate(() => (window as any).desktopTest.emitDownload(2 * 1024 * 1024, null));
  await expect(page.locator('#trialDownloadBar')).not.toHaveAttribute('value');
  await expect(page.locator('#trialDownloadDetails')).toHaveText('已下载 2.0 MB');
  await page.evaluate(() => (window as any).desktopTest.emitTrialProgress('下载完成，正在校验 Copilot 安装包…'));
  await expect(page.locator('#trialDownloadBar')).toBeHidden();
  await page.clock.fastForward(30_000);
  await expect(page.locator('#trialNetworkHint')).toBeHidden();
  await page.evaluate(() => (window as any).desktopTest.releaseSave());
  await expect(page.locator('#wizard')).toBeHidden();
});

test('trial download failure clears progress and allows a fresh retry', async ({ page }) => {
  await launch(page);
  await configure(page, { delaySave: true, failure: 'Copilot 下载失败或连接超时。请检查网络，或开启代理（梯子）后重试。' });
  await page.locator('#trialOpen').click();
  await page.locator('#trialKey').fill('argus_trial_' + 'a'.repeat(64));
  await page.locator('#trialSubmit').click();
  await page.evaluate(() => (window as any).desktopTest.emitDownload(1024, 2048));
  await page.evaluate(() => (window as any).desktopTest.releaseSave());
  await expect(page.locator('#trialDownloadBar')).toBeHidden();
  await expect(page.locator('#trialProgress')).toContainText('代理（梯子）');
  await expect(page.locator('#trialSubmit')).toBeEnabled();
  await configure(page, { failure: '' });
  await page.locator('#trialKey').fill('argus_trial_' + 'a'.repeat(64));
  await page.locator('#trialSubmit').click();
  await page.evaluate(() => (window as any).desktopTest.emitDownload(0, null));
  await expect(page.locator('#trialDownloadDetails')).toHaveText('正在连接下载服务器…');
  await page.evaluate(() => (window as any).desktopTest.releaseSave());
  await expect(page.locator('#wizard')).toBeHidden();
});
