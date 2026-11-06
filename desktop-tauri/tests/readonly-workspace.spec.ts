import { expect, test, type Page } from '@playwright/test';
import { DIFF_CASES, LONG_WORKDIR, NORMAL_DIFF, startReadonlyWorkspaceFixture } from './readonly-workspace-fixture.mjs';

type Fixture = Awaited<ReturnType<typeof startReadonlyWorkspaceFixture>>;
let fixture: Fixture;
let errors: string[];
let outside: string[];
test.beforeEach(async ({ page }) => { errors = []; outside = []; page.on('pageerror', error => errors.push(error.message)); });
test.afterEach(async ({}, info) => {
  if (fixture) {
    await fixture.close();
    await info.attach('request-trace', { body: JSON.stringify(fixture.state.trace, null, 2), contentType: 'application/json' });
    expect(fixture.state.unexpected).toEqual([]);
    expect(fixture.state.trace.filter((row: { method: string }) => !['GET', 'WS'].includes(row.method))).toEqual([]);
    expect(fixture.state.trace.some((row: { path: string }) => /workdir|source-update|operations/.test(row.path))).toBe(false);
  }
  expect(errors).toEqual([]); expect(outside).toEqual([]);
});
async function open(page: Page, width: number, locale: 'en' | 'zh-CN', theme: 'light' | 'dark', kind = 'normal') {
  fixture = await startReadonlyWorkspaceFixture(kind);
  await page.setViewportSize({ width, height: 900 });
  await page.addInitScript(({ locale, theme }) => {
    localStorage.setItem('argus.locale', locale); localStorage.setItem('argus.theme', theme);
    // Capture only the synthetic write, without reading/changing the operator clipboard.
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (value: string) => {
      (window as unknown as { fixtureCopied: string }).fixtureCopied = value;
    } } });
  }, { locale, theme });
  await page.context().route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === fixture.origin) return route.continue();
    outside.push(url.origin + url.pathname); return route.abort('blockedbyclient');
  });
  await page.context().routeWebSocket('**/*', socket => socket.close());
  await page.goto(fixture.url('s-A'));
  await expect(page.locator('.conversation-composer textarea')).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
}
async function ide(page: Page, git = true) {
  await page.getByRole('button', { name: /^(Workbench|工作台)$/ }).filter({ visible: true }).first().click();
  // The current workbench exposes code and runtime; overview/timeline/experiments
  // were removed from WORKBENCH_MODULES, not hidden by this fixture.
  await expect(page.locator('.workbench-module-tab')).toHaveCount(2);
  expect(await page.locator('.workbench-module-tab').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-module'))))
    .toEqual(['ide', 'runtime']);
  await page.locator('[data-module="ide"]').click();
  await expect(page.locator('[data-module="ide"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.ide-v3 .badge').filter({ hasText: /This view is read-only|此视图只读/ })).toBeVisible();
  if (git) await page.locator('.vscode-sc-tabs').getByRole('button', { name: /^(Changes|改动)$/ }).click();
  else await expect(page.locator('.ide-context-strip')).toContainText('Synced');
}

for (const width of [960, 1280, 1920]) for (const locale of ['en', 'zh-CN'] as const) for (const theme of ['light', 'dark'] as const) {
  test(`A3/A4: ${width}px ${locale} ${theme} retains controls, full path, themed diff and raw text`, async ({ page }, info) => {
    await open(page, width, locale, theme);
    const path = page.locator('.project-header [data-session-workdir]');
    const summary = path.locator('summary');
    await expect(summary).toHaveAttribute('aria-label', locale === 'en' ? 'Project folder' : '项目目录');
    await summary.focus(); await page.keyboard.press('Enter');
    await expect(path.locator('.session-workdir__detail')).toBeVisible();
    expect(await path.locator('.session-workdir__detail > code').textContent()).toBe(LONG_WORKDIR);
    // SessionWorkdir now labels only the project folder; the full raw path is
    // still checked above. Idle projects have no resume/stop action.
    await summary.press('Enter');
    await expect(page.locator('[data-stop-work]')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^(Manage project|管理项目)$/ })).toBeInViewport();
    expect(await summary.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await ide(page);
    const diff = page.locator('[data-readonly-diff]');
    const pre = diff.locator('pre');
    await expect(pre).toBeVisible();
    await expect(pre).toHaveAttribute('data-diff-mode', 'highlighted');
    expect(await pre.textContent()).toBe(NORMAL_DIFF);
    await expect(diff.getByRole('status')).toContainText(locale === 'en' ? 'backend truncated' : '已截断');
    const colors = await diff.evaluate(node => {
      const color = (kind: string) => getComputedStyle(node.querySelector(`[data-diff-kind="${kind}"]`)!).color;
      return { addition: color('addition'), deletion: color('deletion'), header: color('header'), context: color('context') };
    });
    expect(colors.addition).not.toBe(colors.deletion);
    expect(colors.header).not.toBe(colors.context);
    await diff.getByRole('button', { name: /^(Original text|原始文本)$/ }).click();
    await expect(pre).toHaveAttribute('data-diff-mode', 'raw');
    expect(await pre.textContent()).toBe(NORMAL_DIFF);
    await diff.getByRole('button', { name: /^(Copy original diff|复制原始差异)$/ }).click();
    expect(await page.evaluate(() => (window as unknown as { fixtureCopied: string }).fixtureCopied)).toBe(NORMAL_DIFF);
    await diff.getByRole('button', { name: /^(Highlighted view|着色视图)$/ }).click();
    expect(await pre.textContent()).toBe(NORMAL_DIFF);
    expect(await page.locator('.ros-content[aria-hidden="false"]').evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await pre.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`workspace-${width}-${locale}-${theme}.png`) });
  });
}

for (const kind of ['empty', 'nongit', 'rename', 'binary', 'html', 'large', 'unknown']) {
  test(`A4: ${kind} content is safe and lossless in a narrow workspace`, async ({ page }, info) => {
    await open(page, 960, 'en', 'dark', kind); await ide(page, kind !== 'nongit');
    if (kind === 'nongit') {
      // Non-Git workspaces retain the explorer but have no source-control panel.
      await expect(page.locator('.vscode-shell')).toHaveClass(/without-git/);
      await expect(page.locator('.vscode-source-control')).toHaveCount(0);
      await expect(page.locator('[data-readonly-diff]')).toHaveCount(0);
      return;
    }
    const diff = page.locator('[data-readonly-diff]');
    const pre = diff.locator('pre');
    await expect(pre).toBeVisible();
    expect(await pre.textContent()).toBe(DIFF_CASES[kind]);
    await expect(pre).toHaveAttribute('data-diff-mode', ['rename', 'html'].includes(kind) ? 'highlighted' : 'raw');
    if (kind === 'html') {
      await expect(pre.locator('script, img')).toHaveCount(0);
      expect(await page.evaluate(() => (window as unknown as { fixtureExecuted?: number }).fixtureExecuted)).toBeUndefined();
    }
    if (kind === 'large') {
      await expect(diff.getByRole('status')).toContainText('backend truncated');
      expect(await pre.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
      await pre.evaluate(node => { node.scrollLeft = node.scrollWidth; });
      expect(await pre.evaluate(node => node.scrollLeft > 0)).toBe(true);
    }
    expect(await page.locator('.ros-content[aria-hidden="false"]').evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`diff-${kind}.png`) });
  });
}
