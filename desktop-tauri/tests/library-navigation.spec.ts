import { expect, test, type Page } from '@playwright/test';
import { startLibraryFixture } from './library-navigation-fixture.mjs';
let fixture: Awaited<ReturnType<typeof startLibraryFixture>>;
let errors: string[], outside: string[];
test.beforeEach(async ({ page }) => { errors = []; outside = []; page.on('pageerror', error => errors.push(error.message)); });
test.afterEach(async ({}, info) => {
  if (fixture) { await fixture.close(); await info.attach('library-request-trace', { body: JSON.stringify(fixture.state.trace, null, 2), contentType: 'application/json' });
    expect(fixture.state.unexpected).toEqual([]);
    expect(fixture.state.trace.filter((row: { method: string }) => !['GET', 'WS'].includes(row.method))).toEqual([]); }
  expect(errors).toEqual([]); expect(outside).toEqual([]);
});
async function sidebar(page: Page) {
  const resources = page.locator('[data-sidebar-resources]');
  if (!await resources.isVisible()) await page.locator('.mobile-tabbar').getByRole('button', { name: /Open projects|打开项目/ }).click();
  await expect(resources).toBeVisible(); return resources;
}
async function open(page: Page, width = 1280, locale = 'zh-CN', theme = 'light', pluginState = 'manual', height = 820) {
  fixture = await startLibraryFixture(pluginState);
  await page.setViewportSize({ width, height });
  await page.addInitScript(({ locale, theme }) => { localStorage.setItem('argus.locale', locale); localStorage.setItem('argus.theme', theme); }, { locale, theme });
  await page.context().route('**/*', route => {
    const url = new URL(route.request().url()); if (url.origin === fixture.origin) return route.continue();
    outside.push(url.origin + url.pathname); return route.abort('blockedbyclient');
  });
  await page.context().routeWebSocket('**/*', socket => socket.close());
  await page.goto(fixture.url('s-A'));
  await expect(page.locator('.conversation-composer textarea')).toBeVisible();
  await page.locator('.conversation-composer textarea').fill('D preview unsent draft');
  return sidebar(page);
}
async function close(page: Page) { await page.getByRole('dialog').getByRole('button', { name: /close|关闭/i }).first().click(); }

for (const width of [960, 1280, 1920]) for (const locale of ['zh-CN', 'en']) for (const theme of ['light', 'dark']) {
  test(`Libraries ${width}px ${locale} ${theme}: top entries, purpose, original text and private knowledge`, async ({ page }, info) => {
    const resources = await open(page, width, locale, theme);
    const zh = locale === 'zh-CN';
    const top = await resources.boundingBox(), search = await page.locator('#daemon-search').boundingBox();
    expect(top!.y + top!.height).toBeLessThanOrEqual(search!.y);
    await expect(resources.getByRole('button', { name: zh ? '插件' : 'Plugins', exact: true })).toBeVisible();
    await resources.getByRole('button', { name: zh ? '技能库' : 'Skill library', exact: true }).click();
    const skills = page.locator('[data-skill-library]');
    await expect(skills.locator('[data-library-introduction]')).toContainText(zh ? '怎么做' : 'how to work');
    await skills.getByRole('navigation').getByRole('button', { name: /^(全局|Global)/ }).click();
    await skills.getByRole('searchbox').fill(zh ? '分段阅读' : 'Progressive PDF Reading');
    await skills.getByRole('button', { name: zh ? /^分段阅读 PDF/ : /^Progressive PDF Reading/ }).click();
    if (zh) {
      await expect(skills.locator('[data-skill-reading-guide]')).toContainText('不是完整执行指令');
      await skills.locator('[data-skill-original] > summary').click();
      await expect(skills.locator('[data-skill-original] h1')).toHaveText('Progressive PDF Reading');
      await skills.locator('[data-skill-original] > summary').click();
    } else {
      await expect(skills.locator('[data-skill-reading-guide]')).toHaveCount(0);
      await expect(skills.getByRole('article')).toContainText('Progressive PDF Reading');
    }
    const requested = fixture.state.trace.filter((row: { path: string }) => row.path.startsWith('/api/skill-library/document?'));
    expect(requested.some((row: { path: string }) => {
      const query = new URL(row.path, fixture.origin).searchParams;
      return query.get('sid') === 's-A' && query.get('library') === 'global:bundled' && query.get('path') === 'engineer/pdf-chat.md';
    })).toBe(true);
    await page.screenshot({ path: info.outputPath(`skill-guide-${width}-${locale}-${theme}.png`) });
    await close(page);
    await resources.getByRole('button', { name: zh ? '知识库' : 'Knowledge base', exact: true }).click();
    const wiki = page.locator('[data-wiki-library]');
    await wiki.getByRole('navigation').getByRole('button', { name: /^(关于你|About you)/ }).click();
    await expect(wiki.locator('[data-wiki-profile]')).toContainText('Synthetic profile stays unchanged.');
    await wiki.getByRole('button', { name: /合成个人记录/ }).click();
    await expect(wiki.locator('[data-knowledge-reading-guide]')).toContainText(zh ? '不是工具使用授权' : 'not authorization');
    await expect(wiki.getByRole('heading', { name: 'SYNTHETIC PERSONAL NOTE', exact: true })).toBeVisible();
    await expect(wiki.getByRole('article')).toContainText('Synthetic original material; not a real operator profile.');
    expect(fixture.state.trace.some((row: { path: string }) => row.path.startsWith('/api/wiki/page?') && new URL(row.path, fixture.origin).searchParams.get('scope') === 'private')).toBe(true);
    expect(await wiki.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`knowledge-guide-${width}-${locale}-${theme}.png`) });
    await close(page);
    await expect(page.locator('.conversation-composer textarea')).toHaveValue('D preview unsent draft');
    await expect(page.locator('[data-resizable-panel="left"]').getByRole('button', { name: zh ? '打开设置' : 'Open settings', exact: true })).toBeInViewport();
    await page.screenshot({ path: info.outputPath(`sidebar-${width}-${locale}-${theme}.png`) });
  });
}

test('short sidebar retains scrollable sessions and existing persisted library folds', async ({ page }) => {
  const resources = await open(page, 960, 'zh-CN', 'dark', 'manual', 600);
  for (const section of ['skills', 'wiki']) {
    const fold = resources.locator(`[data-sidebar-fold="${section}"]`);
    await expect(fold).toHaveAttribute('aria-expanded', 'false'); await fold.click();
    await expect(fold).toHaveAttribute('aria-expanded', 'true');
  }
  expect((await resources.boundingBox())!.height).toBeLessThanOrEqual(241);
  await expect(page.locator('#daemon-search')).toBeInViewport();
  await expect(page.locator('.session-card > button').first()).toBeInViewport();
  await expect(page.locator('[data-resizable-panel="left"]').getByRole('button', { name: '打开设置', exact: true })).toBeInViewport();
  await page.reload(); await sidebar(page);
  for (const section of ['skills', 'wiki']) await expect(page.locator(`[data-sidebar-fold="${section}"]`)).toHaveAttribute('aria-expanded', 'true');
});

for (const state of ['manual', 'ready', 'preparing', 'failed']) test(`existing ${state} plugin behavior remains at the top, without automatic mutations`, async ({ page }) => {
  const resources = await open(page, 1280, 'zh-CN', 'light', state);
  if (state === 'manual') await resources.getByRole('button', { name: '插件', exact: true }).click();
  else {
    const entry = resources.locator('[data-testid="plugin-entry-crystalpilot"]');
    await expect(entry).toBeVisible();
    if (state === 'ready') await expect(entry.getByRole('button', { name: 'CrystalPilot', exact: true })).toBeEnabled();
    if (state === 'preparing') await expect(entry.getByRole('status')).toContainText('正在准备');
    if (state === 'failed') await expect(entry.getByRole('status')).toContainText('合成准备失败');
    await entry.getByRole('button', { name: '管理 CrystalPilot', exact: true }).click();
  }
  const dialog = page.getByRole('dialog'); await expect(dialog).toBeVisible();
  if (state !== 'manual') await expect(dialog.getByRole('button', { name: /^(安装|更新|停用|卸载)$/ })).toHaveCount(0);
  await close(page);
});

test('custom skill originals stay intact when Chinese built-in guides are available', async ({ page }) => {
  const resources = await open(page);
  await resources.getByRole('button', { name: '技能库', exact: true }).click();
  const library = page.locator('[data-skill-library]');
  await library.getByRole('button', { name: /^项目自定步骤/ }).click();
  await expect(library.locator('[data-skill-reading-guide]')).toHaveCount(0);
  await expect(library.getByRole('heading', { name: 'Original custom instructions', exact: true })).toBeVisible();
  await expect(library.getByRole('article')).toContainText('Do not translate or rewrite this original.');
  await close(page);
});
