// Build core and frontend/web first. Run with a fresh evidence directory and external
// PLAYWRIGHT_MODULE / CHROMIUM_EXECUTABLE if browser tools are not installed locally.
import assert from 'node:assert/strict';
import { mkdirSync, existsSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { networkInterfaces } from 'node:os';
import { Store } from '../dist/store.js';
import { keep } from '../dist/serve/files.js';
import { serve } from '../dist/serve/index.js';

assert.ok(process.argv[2], 'Supply a fresh evidence directory');
const output = resolve(process.argv[2]);
const home = join(output, 'home');
assert.ok(!existsSync(home));
mkdirSync(output, { recursive: true });
process.env.ARGUS_HOME = home;
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const store = new Store();
const release = store.own();
const token = 'isolated-preview-browser-token';
const results = [], network = [], errors = [];
let api, browser;
const pageHtml = '<h1 id="module">waiting</h1><p id="before"></p><link rel="stylesheet" href="/style.css"><script type="module" src="/app.js"></script>';
const works = [];
for (const name of ['A', 'B']) {
  const work = store.create({ workdir: join(output, 'work-' + name), request: 'Preview ' + name, backend: 'codex', model: null });
  work.status = 'stopped'; work.sessions = {}; store.save(work);
  writeFileSync(join(work.workdir, 'index.html'), pageHtml);
  writeFileSync(join(work.workdir, 'style.css'), '#module { color: rgb(12, 34, 56); }');
  writeFileSync(join(work.workdir, 'engine.mjs'), `export const value = ${JSON.stringify(name)};`);
  writeFileSync(join(work.workdir, 'app.js'), `import { value } from './engine.mjs'; document.querySelector('#module').textContent = value; document.querySelector('#before').textContent = localStorage.getItem('shared') || 'empty'; localStorage.setItem('shared', value);`);
  // A second delivery of the same page must have another storage origin too.
  for (const title of ['First delivery', 'Second delivery']) {
    const snapshots = keep(work, store.dir(work.id), ['index.html', 'style.css', 'app.js', 'engine.mjs']);
    store.append(work.id, { type: 'handoff', role: 'manager', to: 'operator', title, letter: title, open: ['index.html'], snapshots });
  }
  works.push(work);
}
const auth = { authorization: `Bearer ${token}` };
async function inspect(frame, value) {
  await frame.waitForFunction(expected => document.querySelector('#module')?.textContent === expected, value);
  const state = await frame.evaluate(() => ({ origin: location.origin, value: localStorage.getItem('shared'),
    before: document.querySelector('#before').textContent, token: localStorage.getItem('argus_web_token'),
    color: getComputedStyle(document.querySelector('#module')).color }));
  assert.equal(state.value, value);
  assert.equal(state.token, null);
  assert.equal(state.color, 'rgb(12, 34, 56)');
  return state;
}
async function open(page, cockpit, work) {
  await page.goto(`${cockpit}/?token=${token}&project=${work.id}`);
  await page.locator('.result-card-open').last().click();
  const element = await page.locator('iframe[title="HTML preview: index.html"]').elementHandle();
  return element.contentFrame();
}
try {
  api = await serve({ store, callAgent: () => assert.fail('no model call'), backend: 'codex', model: null,
    port: 0, host: '0.0.0.0', token, web: resolve(import.meta.dirname, '../../../frontend/web/dist') });
  const port = new URL(api.url).port;
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}), args: ['--no-sandbox', '--no-proxy-server'] });
  writeFileSync(join(output, 'configuration.json'), JSON.stringify({ browser: browser.version(), platform: process.platform, port, gpu: false }, null, 2));
  for (const host of ['127.0.0.1', 'localhost']) for (const width of [1440, 1024, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(String(error)));
    page.on('response', response => network.push({ url: response.url(), status: response.status() }));
    const cockpit = `http://${host}:${port}`;
    // Seed both old cockpit origins: neither may become a preview origin.
    for (const alias of ['127.0.0.1', 'localhost']) {
      await page.goto(`http://${alias}:${port}/?token=${token}`);
      await page.waitForFunction(expected => localStorage.getItem('argus_web_token') === expected, token);
    }
    const first = await inspect(await open(page, cockpit, works[0]), 'A');
    const second = await inspect(await open(page, cockpit, works[1]), 'B');
    const revisited = await inspect(await open(page, cockpit, works[0]), 'A');
    assert.notEqual(first.origin, second.origin);
    assert.equal(first.before, 'empty'); assert.equal(second.before, 'empty'); assert.equal(revisited.before, 'A');
    assert.match(new URL(first.origin).hostname, /^[a-z0-9]{32}\.localhost$/);
    assert.equal(new URL(first.origin).port, port);
    const same = await page.request.get(`${cockpit}/api/projects/${works[0].id}/artifact/preview?path=index.html&delivery=d1`, { headers: auth });
    assert.equal(new URL((await same.json()).url).origin, first.origin);
    const old = await page.request.get(`${cockpit}/api/projects/${works[0].id}/artifact/preview?path=index.html&delivery=d0`, { headers: auth });
    const oldUrl = (await old.json()).url;
    assert.notEqual(new URL(oldUrl).origin, first.origin);
    // Top-level storage also stays independent and persists on reload.
    const direct = await context.newPage();
    await direct.goto(oldUrl);
    const oldState = await inspect(direct, 'A');
    assert.equal(oldState.before, 'empty');
    await direct.reload(); assert.equal((await inspect(direct, 'A')).before, 'A');
    await page.screenshot({ path: join(output, `${host}-${width}.png`) });
    results.push({ host, width, first, second, revisited, oldState, separateDeliveries: true });
    await context.close();
  }
  const lan = Object.values(networkInterfaces()).flat().find(address => address?.family === 'IPv4' && !address.internal)?.address;
  if (lan) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    const cockpit = `http://${lan}:${port}`;
    // Relative assets are necessary for the older keyed-path fallback.
    for (const work of works) {
      writeFileSync(join(work.workdir, 'index.html'), pageHtml.replaceAll('href="/', 'href="').replaceAll('src="/', 'src="'));
      const snapshots = keep(work, store.dir(work.id), ['index.html', 'style.css', 'app.js', 'engine.mjs']);
      store.append(work.id, { type: 'handoff', role: 'manager', to: 'operator', title: 'LAN delivery', letter: 'LAN fallback', open: ['index.html'], snapshots });
    }
    const first = await inspect(await open(page, cockpit, works[0]), 'A');
    const second = await inspect(await open(page, cockpit, works[1]), 'B');
    assert.equal(new URL(first.origin).hostname, lan);
    assert.equal(Number(new URL(first.origin).port), Number(port) + 1);
    assert.equal(first.origin, second.origin);
    assert.equal(second.before, 'A');
    results.push({ host: lan, width: 390, fallback: true, first, second, sharedPreviewStorage: true });
    await page.screenshot({ path: join(output, 'lan-390.png') });
    await context.close();
  }
  await api.close();
  api = await serve({ store, callAgent: () => assert.fail('no model call'), backend: 'codex', model: null,
    port: 0, host: '::1', token, web: resolve(import.meta.dirname, '../../../frontend/web/dist') });
  const context = await browser.newContext();
  const page = await context.newPage();
  for (const host of ['[::1]', 'localhost']) {
    const state = await inspect(await open(page, `http://${host}:${new URL(api.url).port}`, works[0]), 'A');
    assert.match(new URL(state.origin).hostname, /^[a-z0-9]{32}\.localhost$/);
    results.push({ host, ipv6ListenerOnly: true, state });
  }
  await context.close();
  assert.deepEqual(errors, []);
  console.log('Passed both loopback cockpit entry points, modules/styles, project and delivery storage isolation; measured LAN fallback shared storage.');
} finally {
  writeFileSync(join(output, 'results.json'), JSON.stringify({ results, network, errors }, null, 2));
  await browser?.close(); await api?.close(); release();
}
