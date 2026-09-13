import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';
import { verifyColdStartupTiming, verifyEyeMotion } from '../scripts/preview-eye-checks.mjs';

async function launch(page: Page, complete = false, options: { state?: string; cockpitReady?: Promise<void>; theme?: 'light' | 'dark' | 'system'; nativeVisible?: boolean; eyeMotion?: 'on' | 'off' | 'system'; delayAppearance?: boolean } = {}) {
  await page.route('**/bridge.ts', (route) => route.fulfill({
    contentType: 'application/javascript',
    body: 'export const desktopBridge = window.desktopTest.bridge;',
  }));
  await page.route('http://127.0.0.1:18880/**', async (route) => {
    if (options.cockpitReady) await options.cockpitReady;
    await route.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><title>Test cockpit</title><input id="draft" aria-label="Draft"><p>Manager · Engineer</p>',
    });
  });
  await page.addInitScript(({ configured, initialState, theme, nativeVisible, eyeMotion, delayAppearance }) => {
    const callbacks: Record<string, (value: unknown) => void> = {};
    const state = {
      status: { state: initialState || (configured ? 'ready' : 'idle'), message: configured ? '已就绪' : '请选择 Agent CLI' },
      complete: configured,
      nativeVisible,
      eyeMotion,
      delayAppearance,
      releaseAppearance: null as (() => void) | null,
      motionSaves: 0,
      motionError: false,
      activate: () => callbacks.launch?.(undefined),
      download: (value: unknown) => callbacks.download?.(value),
      pickerCalls: [] as string[],
      pickerPath: 'D:/中文 research/sample',
      trialMode: false,
      failure: '',
      saveCount: 0,
      saved: null as unknown,
      delaySave: false,
      url: 'http://127.0.0.1:18880/',
      savedAppearance: null as unknown,
      releaseSave: null as (() => void) | null,
      emit: (value: { state: string; message: string }) => {
        state.status = value;
        callbacks.status?.(value);
      },
      bridge: {} as Record<string, unknown>,
    };
    state.bridge = {
      getStatus: async () => state.status,
      isWindowVisible: async () => state.nativeVisible,
      onLaunchActivation: (callback: (value: unknown) => void) => { callbacks.launch = callback; },
      chooseLocalPath: async (kind: string) => { state.pickerCalls.push(kind); return state.pickerPath; },
      getAppearance: async () => {
        if (state.delayAppearance) await new Promise<void>(resolve => { state.releaseAppearance = resolve; });
        return { theme, resolvedTheme: theme === 'dark' ? 'dark' : 'light', startupEyeMotion: state.eyeMotion };
      },
      setStartupEyeMotion: async (motion: 'on' | 'off' | 'system') => {
        if (state.motionError) throw new Error('Synthetic preference save failure');
        state.motionSaves++;
        state.eyeMotion = motion;
        return { theme, resolvedTheme: theme === 'dark' ? 'dark' : 'light', startupEyeMotion: motion };
      },
      setWindowTheme: async () => undefined,
      setAppearance: async (appearance: unknown) => { state.savedAppearance = appearance; return appearance; },
      getSetup: async () => ({
        complete: state.complete, trialMode: state.trialMode, canRestoreOwnAccount: configured,
        host: '127.0.0.1', port: 18880,
        runnerKind: 'codex', runnerConfigured: state.complete,
        runnerBins: {}, detectedRunners: { codex: 'C:/agents/codex.cmd', pi: 'C:/agents/pi.cmd' },
        piConfiguration: { configDir: '' },
        releaseIdentity: { packageVersion: '0.1.1', releaseId: 'test', sourceDigest: 'test', distribution: 'preview' },
        runtimeIdentity: { state: state.status.state },
      }),
      openCockpit: async () => state.url,
      onTrialProgress: (callback: (value: unknown) => void) => { callbacks.trialProgress = callback; },
      onTrialDownload: (callback: (value: unknown) => void) => { callbacks.download = callback; },
      getTrialStatus: async () => ({ tokensRemaining: 900000, tokenLimit: 1000000, stale: false }),
      resumeTrial: async () => ({ tokensRemaining: 900000, tokenLimit: 1000000, stale: false }),
      restoreOwnAccount: async () => { state.trialMode = false; return { ok: true }; },
      completeTrialSetup: async () => {
        state.saveCount++;
        if (state.delaySave) await new Promise<void>((resolve) => { state.releaseSave = resolve; });
        if (state.failure) return { ok: false, error: state.failure };
        state.trialMode = true;
        state.complete = true;
        state.emit({ state: 'ready', message: '已就绪' });
        return { ok: true };
      },
      completeSetup: async (input: unknown) => {
        state.saveCount++;
        state.saved = input;
        if (state.delaySave) await new Promise<void>((resolve) => { state.releaseSave = resolve; });
        if (state.failure) return { ok: false, error: state.failure };
        state.complete = true;
        state.emit({ state: 'ready', message: '已就绪' });
        return { ok: true };
      },
      restartBackend: async () => state.emit({ state: 'ready', message: '已就绪' }),
      chooseRunner: async () => 'C:/custom/codex.cmd',
      getUpdateStatus: async () => ({ state: 'idle', currentVersion: '0.1.1', userInitiated: false }),
      checkForUpdate: async () => ({ state: 'idle', currentVersion: '0.1.1', userInitiated: true, detail: '预览构建不安装发布更新。' }),
      dismissUpdate: async () => undefined,
      onStatus: (callback: (value: unknown) => void) => { callbacks.status = callback; },
      onUpdateStatus: () => undefined,
      onShowSetup: () => undefined,
      onNewChat: () => undefined,
      onOpenDelivery: () => undefined,
    };
    (window as unknown as { desktopTest: typeof state }).desktopTest = state;
  }, { configured: complete, initialState: options.state, theme: options.theme || 'light', nativeVisible: options.nativeVisible ?? true, eyeMotion: options.eyeMotion || 'on', delayAppearance: options.delayAppearance ?? false });
  await page.goto('/', { waitUntil: options.cockpitReady ? 'domcontentloaded' : 'load' });
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

test('trial byte progress remains available in the exclusive account-mode layout', async ({ page }) => {
  await launch(page);
  await page.locator('#trialOpen').click();
  await configure(page, { delaySave: true });
  await page.locator('#trialKey').fill(`argus_trial_${'a'.repeat(64)}`);
  await page.locator('#trialSubmit').click();
  await page.evaluate(() => (window as any).desktopTest.download({ downloaded_bytes: 1024, total_bytes: 2048 }));
  await expect(page.locator('#trialDownload')).toBeVisible();
  await expect(page.locator('#trialDownloadBar')).toHaveJSProperty('value', 50);
  await expect(page.locator('#trialDownloadDetails')).toContainText('50%');
  await expect(page.locator('#ownContent')).toBeHidden();
  await page.evaluate(() => (window as any).desktopTest.download({ downloaded_bytes: 2048, total_bytes: null }));
  await expect(page.locator('#trialDownloadBar')).not.toHaveAttribute('value');
  await page.evaluate(() => (window as any).desktopTest.releaseSave());
  await expect(page.locator('#wizard')).toBeHidden();
});

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
  const frame = page.frames().find((item) => item.url().startsWith('http://127.0.0.1:18880/'))!;
  expect(await frame.evaluate(() => {
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

test('a new desktop profile starts light even when the operating system prefers dark', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await launch(page);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(249, 250, 251)');
  await expect(page.locator('#wizard')).toBeVisible();
});

for (const theme of ['dark', 'system'] as const) {
  test(`an explicitly saved ${theme} preference survives the new first-run default`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await launch(page, true, { theme });
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(page.locator('#cockpit')).toBeVisible();
    expect(await page.evaluate(() => (window as any).desktopTest.savedAppearance)).toBeNull();
  });
}

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

for (const theme of ['light', 'dark']) {
  test(`account choice has one focused panel and no added eye decoration (${theme})`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 960, height: 640 });
    await launch(page);
    await expect(page.locator('#wizard')).toBeVisible();
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await expect(page.locator('#ownOpen')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#trialPanel')).toBeHidden();
    await expect(page.locator('#ownContent')).toBeVisible();
    const layout = await page.evaluate(() => {
      // Sample both boxes in one frame; the original wizard entrance animation
      // translates the whole dialog between separate Playwright round trips.
      const own = document.getElementById('ownOpen')!.getBoundingClientRect();
      const trial = document.getElementById('trialOpen')!.getBoundingClientRect();
      return { topDifference: Math.abs(own.y - trial.y), widthDifference: Math.abs(own.width - trial.width) };
    });
    expect(layout.topDifference).toBeLessThan(1);
    expect(layout.widthDifference).toBeLessThan(1);
    await expect(page.locator('#wizardNext')).toBeInViewport();
    await expect(page.locator('.argus-splash-eye circle')).toHaveCount(2);
    await expect(page.locator('.argus-wizard-eye circle')).toHaveCount(2);
    await expect(page.locator('.argus-loading-ring')).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath('own-account.png'), animations: 'disabled' });
    await page.locator('#trialOpen').click();
    await expect(page.locator('#ownContent')).toBeHidden();
    await expect(page.locator('#trialPanel')).toBeVisible();
    await expect(page.locator('#trialKey')).toBeInViewport();
    await expect(page.locator('#trialSubmit')).toBeInViewport();
    await expect(page.locator('#wizardNext')).toBeHidden();
    await page.screenshot({ path: testInfo.outputPath('trial-account.png'), animations: 'disabled' });
    await page.locator('#trialKey').fill('discard-on-mode-change');
    await page.locator('#ownOpen').click();
    await expect(page.locator('#trialKey')).toHaveValue('');
    await expect(page.locator('#ownContent')).toBeVisible();
  });
}

test('trial form reports failures and can complete without repeating own-account setup', async ({ page }) => {
  await launch(page);
  await page.locator('#trialOpen').click();
  await page.locator('#trialKey').fill('invalid');
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#trialProgress')).toContainText('完整的内部测试 Key');
  await configure(page, { failure: '内测 Key 未被识别' });
  await page.locator('#trialKey').fill('argus_trial_' + 'e'.repeat(64));
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#trialKey')).toHaveValue('');
  await expect(page.locator('#trialProgress')).toContainText('未被识别');
  await configure(page, { failure: '' });
  await page.locator('#trialKey').fill('argus_trial_' + 'e'.repeat(64));
  await page.locator('#trialSubmit').click();
  await expect(page.locator('#cockpit')).toBeVisible();
  await expect(page.locator('#wizard')).toBeHidden();
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

function eyeOutput(testInfo: { outputPath: (name: string) => string }) {
  const directory = testInfo.outputPath('eye-evidence');
  mkdirSync(directory, { recursive: true });
  return directory;
}

test('configured cold start exposes moving pupil pixels before revealing the cockpit', async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await launch(page, true);
  await verifyEyeMotion(page, '.argus-splash-eye', {
    directory: eyeOutput(testInfo), label: 'cold', duration: 400, minimumExcursion: 0.02,
  });
  const timing = await verifyColdStartupTiming(page);
  expect(timing.visibleMilliseconds).toBeGreaterThanOrEqual(1030);
  await expect(page.locator('#splash')).toBeHidden();
});

test('first-run pupil visibly orbits using only the original circles', async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await launch(page);
  await verifyEyeMotion(page, '.argus-wizard-eye', { directory: eyeOutput(testInfo), label: 'wizard' });
  await expect(page.locator('.argus-wizard-eye circle')).toHaveCount(2);
  await expect(page.locator('.argus-loading-ring')).toHaveCount(0);
});

for (const surface of ['splash', 'wizard']) {
  test(`system mode respects reduced motion and leaves the ${surface} eye centered and still`, async ({ page }, testInfo) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await launch(page, surface === 'splash', { eyeMotion: 'system', ...(surface === 'splash' ? { state: 'starting' } : {}) });
    const eye = page.locator(`.argus-${surface}-eye`);
    await expect(eye).toHaveCSS('animation-name', 'none');
    await expect(eye).toHaveCSS('transform', 'none');
    await verifyEyeMotion(page, `.argus-${surface}-eye`, {
      directory: eyeOutput(testInfo), label: `reduced-${surface}`, duration: 350, moving: false,
    });
  });
}

test('system-mode reduced-motion cold start has no minimum animation delay', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await launch(page, true, { eyeMotion: 'system' });
  const timing = await verifyColdStartupTiming(page);
  expect(timing.visibleMilliseconds).toBeLessThan(1000);
  await expect(page.locator('#splash')).toBeHidden();
});

for (const surface of ['splash', 'wizard']) {
  test(`explicit eye On moves the ${surface} pupil even when the system reduces motion`, async ({ page }, testInfo) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await launch(page, surface === 'splash', { eyeMotion: 'on', ...(surface === 'splash' ? { state: 'starting' } : {}) });
    await verifyEyeMotion(page, `.argus-${surface}-eye`, {
      directory: eyeOutput(testInfo), label: `enabled-${surface}`, moving: true,
    });
    // Other transitions are still reduced; only the original eye is opted in.
    await expect(page.locator('.desktop-menu-trigger').first()).toHaveCSS('transition-duration', '1e-05s');
  });
}

test('default On cold start keeps its full visible cycle under reduced motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await launch(page, true);
  const timing = await verifyColdStartupTiming(page);
  expect(timing).toMatchObject({ reducedMotion: true, eyeMode: 'on', motionEnabled: true });
  expect(timing.visibleMilliseconds).toBeGreaterThanOrEqual(1030);
});

test('Off stays still even when the system allows animation', async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await launch(page, true, { state: 'starting', eyeMotion: 'off' });
  await verifyEyeMotion(page, '.argus-splash-eye', { directory: eyeOutput(testInfo), label: 'explicit-off', duration: 350, moving: false });
});

test('a saved Off choice cannot flash animation before its native preference arrives', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await launch(page, true, { eyeMotion: 'off', delayAppearance: true });
  await expect(page.locator('html')).toHaveAttribute('data-startup-eye-motion', 'pending');
  await expect(page.locator('.argus-splash-eye')).toHaveCSS('animation-name', 'none');
  await page.evaluate(() => (window as any).desktopTest.releaseAppearance());
  await expect(page.locator('html')).toHaveAttribute('data-startup-eye-motion', 'off');
  await expect(page.locator('#cockpit')).toBeVisible();
});

test('eye preference saves independently without restarting setup or losing a draft', async ({ page }) => {
  await launch(page, true);
  await expect(page.locator('#splash')).toBeHidden();
  await page.frameLocator('#cockpitFrame').locator('#draft').fill('retain this draft');
  await page.locator('#fileMenuTrigger').click();
  await expect(page.locator('button[data-startup-eye-motion="on"]')).toHaveAttribute('aria-checked', 'true');
  await page.locator('button[data-startup-eye-motion="off"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-startup-eye-motion', 'off');
  await expect(page.frameLocator('#cockpitFrame').locator('#draft')).toHaveValue('retain this draft');
  expect(await page.evaluate(() => (window as any).desktopTest.motionSaves)).toBe(1);
  expect(await page.evaluate(() => (window as any).desktopTest.saveCount)).toBe(0);
});

test('failed eye preference save preserves the previous motion choice', async ({ page }) => {
  await launch(page, true);
  await expect(page.locator('#splash')).toBeHidden();
  await configure(page, { motionError: true });
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#fileMenuTrigger').click();
  await page.locator('button[data-startup-eye-motion="off"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-startup-eye-motion', 'on');
  expect(await page.evaluate(() => (window as any).desktopTest.motionSaves)).toBe(0);
});

test('a slow iframe stays behind the splash even after the visible cycle', async ({ page }) => {
  let release!: () => void;
  const cockpitReady = new Promise<void>(resolve => { release = resolve; });
  await launch(page, true, { cockpitReady });
  await page.waitForTimeout(1200);
  await expect(page.locator('#cockpit')).toBeHidden();
  await expect(page.locator('.argus-splash-eye')).toBeVisible();
  release();
  await expect(page.locator('#cockpit')).toBeVisible();
  await expect(page.locator('#splash')).toBeHidden();
});

test('a late iframe load cannot conceal a newer startup error', async ({ page }) => {
  let release!: () => void;
  const cockpitReady = new Promise<void>(resolve => { release = resolve; });
  await launch(page, true, { cockpitReady });
  await page.evaluate(() => (window as any).desktopTest.emit({ state: 'error', message: 'Fixture startup failure' }));
  release();
  await page.waitForTimeout(1250);
  await expect(page.locator('#retry')).toBeVisible();
  await expect(page.locator('#cockpit')).toBeHidden();
  await expect(page.locator('.argus-splash-eye')).toHaveCSS('animation-play-state', 'paused');
  await page.evaluate(() => (window as any).desktopTest.emit({ state: 'ready', message: '已就绪' }));
  await expect(page.locator('#cockpit')).toBeVisible();
});

test('a hidden native window cannot spend the visible cold-start cycle', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await launch(page, true, { nativeVisible: false });
  await page.waitForTimeout(1250);
  await expect(page.locator('#cockpit')).toBeHidden();
  expect(await page.evaluate(() => performance.getEntriesByName('argus:splash-visible').length)).toBe(0);
  await configure(page, { nativeVisible: true });
  const timing = await verifyColdStartupTiming(page);
  expect(timing.visibleMilliseconds).toBeGreaterThanOrEqual(1030);
});

test('explicit EXE reactivation replays the eye without replacing the live draft', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await launch(page, true);
  await expect(page.locator('#splash')).toBeHidden();
  await page.frameLocator('#cockpitFrame').locator('#draft').fill('retained research draft');
  await page.evaluate(() => (window as any).desktopTest.activate());
  await expect(page.locator('.argus-splash-eye')).toBeVisible();
  await expect(page.locator('#cockpit')).toBeHidden();
  await verifyColdStartupTiming(page);
  await expect(page.frameLocator('#cockpitFrame').locator('#draft')).toHaveValue('retained research draft');
  expect(await page.evaluate(() => (window as any).desktopTest.saveCount)).toBe(0);
});

test('only the current cockpit can request a bounded path picker', async ({ page }) => {
  await launch(page, true);
  await expect(page.locator('#splash')).toBeHidden();
  const message = { type: 'argus:choose-path', version: 1, requestId: 'test-picker', kind: 'folder' };
  await page.evaluate(value => window.postMessage(value, '*'), message);
  expect(await page.evaluate(() => (window as any).desktopTest.pickerCalls)).toEqual([]);
  const frame = page.frames().find(item => item.url().startsWith('http://127.0.0.1:18880/'))!;
  const result = await frame.evaluate(value => new Promise(resolve => {
    window.addEventListener('message', event => {
      if (event.data?.type === 'argus:path-result') resolve(event.data);
    }, { once: true });
    parent.postMessage(value, '*');
  }), message);
  expect(result).toMatchObject({ requestId: 'test-picker', path: 'D:/中文 research/sample', cancelled: false });
  expect(await page.evaluate(() => (window as any).desktopTest.pickerCalls)).toEqual(['folder']);
  await frame.evaluate(() => parent.postMessage({ type: 'argus:choose-path', version: 1, requestId: 'bad', kind: 'shell' }, '*'));
  expect(await page.evaluate(() => (window as any).desktopTest.pickerCalls)).toEqual(['folder']);
});

test('startup errors pause visible eye pixels and retry can resume them', async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await launch(page, true, { state: 'starting', eyeMotion: 'on' });
  await page.waitForTimeout(200);
  await page.evaluate(() => (window as any).desktopTest.emit({ state: 'error', message: 'Fixture error' }));
  const directory = eyeOutput(testInfo);
  await verifyEyeMotion(page, '.argus-splash-eye', { directory, label: 'error', duration: 350, moving: false });
  await page.evaluate(() => (window as any).desktopTest.emit({ state: 'starting', message: '正在恢复' }));
  await verifyEyeMotion(page, '.argus-splash-eye', { directory, label: 'retry' });
});
