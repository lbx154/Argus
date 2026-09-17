import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const adapter = readFileSync(new URL('../../argus/webapi/plugin_desktop.js', import.meta.url), 'utf8');
const pluginOrigin = 'http://127.0.0.1:18890';
const fixture = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body>
<form><input aria-label="项目路径" value="D:/original"><button data-testid="browse-folder" type="button">浏览…</button></form>
<label>CIF<input type="file" accept=".cif"></label>
<script>window.fallbackClicks=0; window.changedPath='D:/original';
 document.querySelector('button').addEventListener('click',()=>window.fallbackClicks++);
 document.querySelector('form input').addEventListener('input',event=>window.changedPath=event.target.value);
</script><script>${adapter}</script></body></html>`;

async function open(page: Page, result: Record<string, unknown> = { path: 'D:\\中文 research\\sample', cancelled: false }, native = true) {
  await page.route(pluginOrigin + '/**', route => route.fulfill({ contentType: 'text/html', body: fixture }));
  await page.route('http://tauri.localhost/**', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="utf-8">
<iframe id="plugin" src="${pluginOrigin}/" sandbox="allow-forms allow-same-origin allow-scripts allow-downloads"></iframe>
<script>window.requests=[]; const iframe=document.getElementById('plugin');
addEventListener('message',event=>{
 if(event.source!==iframe.contentWindow || event.origin!==${JSON.stringify(pluginOrigin)})return;
 const data=event.data;
 if(data.type==='argus:path-capabilities'){
   iframe.contentWindow.postMessage({type:data.type,requestId:data.requestId,version:1,kinds:['folder','cif']},event.origin);
 }else if(data.type==='argus:choose-path'){
   window.requests.push(data);
   iframe.contentWindow.postMessage({type:'argus:path-result',requestId:data.requestId,...${JSON.stringify(result)}},event.origin);
 }
});</script>` }));
  await page.goto(native ? 'http://tauri.localhost/' : pluginOrigin + '/');
  const frame = native ? page.frames().find(frame => frame.url().startsWith(pluginOrigin))! : page.mainFrame();
  await expect(frame.locator('[data-testid="browse-folder"]')).toBeVisible();
  if (native) await expect(frame.locator('[data-testid="browse-folder"]')).toHaveAttribute('data-argus-native-picker', 'ready');
  return frame;
}

test.afterEach(async ({ page }, info) => {
  if (info.status === info.expectedStatus) return;
  const frame = page.frames().find(item => item.url().startsWith(pluginOrigin));
  const diagnostics = {
    requests: await page.evaluate(() => (window as any).requests).catch(() => null),
    state: await frame?.evaluate(() => ({
      path: document.querySelector('input')?.value,
      changedPath: (window as any).changedPath,
      fallbackClicks: (window as any).fallbackClicks,
      disabled: document.querySelector('button')?.disabled,
      status: document.querySelector('[data-argus-picker-status]')?.textContent,
    })).catch(() => null),
  };
  await info.attach('picker-fixture-state', { body: JSON.stringify(diagnostics), contentType: 'application/json' });
});

test('plugin Browse uses the native folder request and updates the public form without submitting', async ({ page }) => {
  const frame = await open(page);
  await frame.locator('[data-testid="browse-folder"]').click();
  await expect(frame.getByRole('textbox', { name: '项目路径' })).toHaveValue('D:\\中文 research\\sample');
  expect(await frame.evaluate(() => (window as any).changedPath)).toBe('D:\\中文 research\\sample');
  expect(await frame.evaluate(() => (window as any).fallbackClicks)).toBe(0);
  expect(await page.evaluate(() => (window as any).requests)).toMatchObject([{ kind: 'folder', version: 1 }]);
});

test('cancelling the native picker preserves the existing path', async ({ page }) => {
  const frame = await open(page, { path: null, cancelled: true });
  await frame.locator('[data-testid="browse-folder"]').click();
  await expect(frame.getByRole('textbox', { name: '项目路径' })).toHaveValue('D:/original');
  await expect(frame.locator('[data-testid="browse-folder"]')).toBeEnabled();
});

test('native failure is explicit and leaves manual entry available', async ({ page }) => {
  const frame = await open(page, { error: '系统选择窗口不可用' });
  await frame.locator('[data-testid="browse-folder"]').click();
  await expect(frame.getByRole('status')).toContainText('系统选择窗口不可用');
  await frame.getByRole('textbox', { name: '项目路径' }).fill('E:/manual');
  expect(await frame.evaluate(() => (window as any).changedPath)).toBe('E:/manual');
});

test('a normal browser retains the plugin folder browser', async ({ page }) => {
  const frame = await open(page, {}, false);
  await frame.locator('[data-testid="browse-folder"]').click();
  expect(await frame.evaluate(() => (window as any).fallbackClicks)).toBe(1);
  await expect(frame.locator('[data-testid="browse-folder"]')).not.toHaveAttribute('data-argus-native-picker', 'ready');
});

test('synthetic clicks cannot open a system dialog', async ({ page }) => {
  const frame = await open(page);
  await frame.locator('[data-testid="browse-folder"]').evaluate((button: HTMLButtonElement) => button.click());
  expect(await page.evaluate(() => (window as any).requests)).toEqual([]);
});

test('CIF file selection still opens the browser file chooser inside the sandbox', async ({ page }) => {
  const frame = await open(page);
  const chooser = page.waitForEvent('filechooser');
  await frame.locator('input[type="file"]').click();
  await (await chooser).setFiles({ name: '中文 sample.cif', mimeType: 'chemical/x-cif', buffer: Buffer.from('data_fixture\n') });
  expect(await frame.locator('input[type="file"]').evaluate((input: HTMLInputElement) => input.files?.[0]?.name)).toBe('中文 sample.cif');
  expect(await page.evaluate(() => (window as any).requests)).toEqual([]);
});
