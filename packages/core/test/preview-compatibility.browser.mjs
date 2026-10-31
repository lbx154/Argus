// Build core and web first; pass a fresh evidence directory. Uses external Playwright/Chromium.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { existsSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { join, resolve } from 'node:path';
import { Store } from '../dist/store.js';
import { keep } from '../dist/serve/files.js';
import { serve } from '../dist/serve/index.js';

assert.ok(process.argv[2], 'Supply a fresh evidence directory');
const output = resolve(process.argv[2]);
mkdirSync(output, { recursive: true });
const store = new Store(join(output, 'home'));
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const work = store.create({ workdir: join(output, 'site'), request: 'Check a delivered website', backend: 'codex', model: null });
work.status = 'stopped'; work.sessions = {}; store.save(work);
mkdirSync(join(work.workdir, 'site/deep'), { recursive: true });
const files = {
  'site/deep/index.html': '<h1 id="value">waiting</h1><link rel="stylesheet" href="../style.css"><script type="module" src="../app.js"></script>',
  'site/style.css': '#value { color: rgb(12, 34, 56); }',
  'site/engine.js': 'export const value = "ready";',
  'site/app.js': 'import {value} from "./engine.js"; document.querySelector("#value").textContent=value; document.cookie="plain=1; path=/"; localStorage.setItem("value",value);',
  'site/unused.js': 'not requested',
};
for (const [name, text] of Object.entries(files)) writeFileSync(join(work.workdir, name), text);
const snapshots = keep(work, store.dir(work.id), Object.keys(files));
store.append(work.id, { type: 'handoff', role: 'manager', to: 'operator', letter: 'Delivered website', open: ['site/deep/index.html'], snapshots });
const results = [];
let api, browser, extracted, vite;
const stopProcess = async child => { if (child?.pid && child.exitCode === null && child.signalCode === null) { const ended = once(child, 'exit'); child.kill(); await ended; } };
try {
  api = await serve({ store, callAgent: () => assert.fail('no model call'), backend: 'codex', model: null,
    port: 0, host: '::', token: 'preview-browser', web: resolve(import.meta.dirname, '../web') });
  const port = new URL(api.url).port;
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}), args: ['--no-sandbox', '--no-proxy-server'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  for (const host of ['127.0.0.1', '0.0.0.0', '[::]']) {
    await page.goto(`http://${host}:${port}/?token=preview-browser&project=${work.id}`);
    await page.locator('.result-card-open').last().click();
    const iframe = page.locator('iframe[title="HTML preview: index.html"]');
    const frame = await iframe.elementHandle().then(element => element.contentFrame());
    await frame.locator('#value').getByText('ready', { exact: true }).waitFor();
    const previewUrl = frame.url();
    assert.match(new URL(previewUrl).hostname, /^[0-9a-f]{32}\.localhost$/);
    assert.equal(new URL(previewUrl).port, port);
    await page.getByText(/macOS 15/).waitFor();
    const open = page.locator(`a[href="${previewUrl}"][target="_blank"]`);
    assert.equal(await open.getAttribute('rel'), 'noopener noreferrer');
    const cookieInFrame = await frame.evaluate(() => document.cookie);
    assert.equal(cookieInFrame.includes('plain=1'), false);
    const popupPromise = context.waitForEvent('page');
    await open.click();
    const popup = await popupPromise;
    await popup.locator('#value').getByText('ready', { exact: true }).waitFor();
    assert.equal(popup.url(), previewUrl);
    assert.match(await popup.evaluate(() => document.cookie), /plain=1/);
    assert.equal(await popup.evaluate(() => getComputedStyle(document.querySelector('#value')).color), 'rgb(12, 34, 56)');
    const other = store.create({ workdir: join(output, 'other-' + host.replace(/\W/g, '')), request: '', backend: 'codex', model: null });
    writeFileSync(join(other.workdir, 'index.html'), '<p>Other project</p>');
    const otherUrl = await page.evaluate(async id => (await (await fetch(`/api/projects/${id}/artifact/preview?path=index.html`, { headers: { authorization: 'Bearer preview-browser' } })).json()).url, other.id);
    await popup.goto(otherUrl);
    assert.equal(await popup.evaluate(() => localStorage.getItem('value')), null);
    await popup.close();
    results.push({ host, previewUrl, cookieInFrame, newTabCookie: 'plain=1', projectStorageIsolated: true });
    if (host === '127.0.0.1') {
      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('button', { name: /Download page|下载网页/ }).click();
      const download = await downloadPromise;
      const archive = join(output, 'website.zip');
      await download.saveAs(archive);
      await new Promise((accept, reject) => {
        const unzip = spawn('python3', ['-c', 'import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', archive, join(output, 'extracted')]);
        unzip.once('error', reject); unzip.once('exit', code => code === 0 ? accept() : reject(new Error('unzip failed')));
      });
      assert.equal(readFileSync(join(output, 'extracted/deep/index.html'), 'utf8'), files['site/deep/index.html']);
      assert.equal(readFileSync(join(output, 'extracted/app.js'), 'utf8'), files['site/app.js']);
      assert.equal(existsSync(join(output, 'extracted/unused.js')), false, 'Archive must include only files actually served');
      const socket = createServer();
      await new Promise(accept => socket.listen(0, '127.0.0.1', accept));
      const downloadPort = socket.address().port;
      await new Promise(accept => socket.close(accept));
      extracted = spawn('python3', ['-u', '-m', 'http.server', String(downloadPort), '--bind', '127.0.0.1', '--directory', join(output, 'extracted')]);
      await new Promise((accept, reject) => { extracted.stdout.once('data', accept); extracted.once('error', reject); extracted.once('exit', () => reject(new Error('http.server failed'))); });
      const extractedPage = await context.newPage();
      await extractedPage.goto(`http://127.0.0.1:${downloadPort}/deep/index.html`);
      await extractedPage.locator('#value').getByText('ready', { exact: true }).waitFor();
      assert.equal(await extractedPage.evaluate(() => getComputedStyle(document.querySelector('#value')).color), 'rgb(12, 34, 56)');
      await extractedPage.reload();
      assert.equal(await extractedPage.evaluate(() => localStorage.getItem('value')), 'ready');
      await extractedPage.close();
      await stopProcess(extracted); extracted = undefined;
      results.push({ actualZipDownloadedAndExtractedPagePassed: true });
    }
  }
  // Preserve the real Vite changeOrigin:true path and authorize by the browser's Origin.
  const headers = { authorization: 'Bearer preview-browser' };
  const stopPath = `/api/projects/${work.id}/daemon/stop`;
  assert.equal((await fetch(`http://127.0.0.1:${port}${stopPath}`, { method: 'POST', headers })).status, 200);
  vite = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '0'], {
    cwd: resolve(import.meta.dirname, '../../../frontend/web'), env: { ...process.env, ARGUS_WEB_API: `http://127.0.0.1:${port}` },
  });
  const viteUrl = await new Promise((accept, reject) => {
    let output = '';
    vite.stdout.on('data', chunk => { output += chunk; const found = /http:\/\/127\.0\.0\.1:\d+/.exec(output); if (found) accept(found[0]); });
    vite.once('error', reject); vite.once('exit', () => reject(new Error(output)));
  });
  await page.goto(viteUrl);
  const proxyStatus = await page.evaluate(async path => (await fetch(path, { method: 'POST', headers: { authorization: 'Bearer preview-browser' } })).status, stopPath);
  assert.equal(proxyStatus, 200);
  const previewOrigin = new URL(results[0].previewUrl).origin;
  assert.equal((await fetch(viteUrl + stopPath, { method: 'POST', headers: { ...headers, origin: previewOrigin } })).status, 403);
  results.push({ directStop: 200, viteStop: proxyStatus, vitePreviewOriginRejected: 403 });
  writeFileSync(join(output, 'browser.json'), JSON.stringify({ browser: browser.version(), platform: process.platform, width: 1440, results }, null, 2));
  console.log('Passed local entry, compatibility notice, new tab/cookie contrast, project isolation, actual ZIP replay and Vite 200/200/403.');
} finally {
  writeFileSync(join(output, 'results.json'), JSON.stringify(results, null, 2));
  await stopProcess(vite); await stopProcess(extracted);
  await browser?.close(); await api?.close();
}
