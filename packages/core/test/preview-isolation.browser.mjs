// Run after the core and packaged web builds. Supply a fresh evidence directory as argv[2].
// PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE may point at shared tools outside this repository.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { Store } from '../dist/store.js';
import { keep } from '../dist/serve/files.js';
import { serve } from '../dist/serve/index.js';

assert.ok(process.argv[2], 'Pass a fresh evidence directory.');
const output = resolve(process.argv[2]);
const home = join(output, 'home');
assert.ok(!existsSync(home), 'Choose an evidence directory without an existing home.');
mkdirSync(output, { recursive: true });
process.env.ARGUS_HOME = home;
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const store = new Store();
const release = store.own();
const results = [];
let api, browser;

// A simple cross-origin POST reaches the server without a CORS preflight. A CORS read failure
// says nothing about whether it created a durable project, so inspect the on-disk directories.
async function probe(url) {
  try { await fetch(url, { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: '{}' }); }
  catch { /* The response can be unreadable even though the POST was executed. */ }
  document.querySelector('#done').textContent = 'finished';
}
const third = createServer((_request, response) => {
  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }).end(
    `<h1>Third-origin widget</h1><p id="done">waiting</p><script>(${probe})(${JSON.stringify(api.url + '/api/daemons')})</script>`,
  );
});
const projectDirectories = () => readdirSync(join(home, 'projects')).filter(name => /^p-[0-9a-f]+$/.test(name));
try {
  await new Promise((accept, reject) => third.once('error', reject).listen(0, '127.0.0.1', accept));
  const thirdOrigin = `http://127.0.0.1:${third.address().port}`;
  const work = store.create({ workdir: join(output, 'delivery'), request: 'Verify a delivered page embedding a third-origin widget.', backend: 'codex', model: null });
  work.status = 'stopped'; work.sessions = {}; store.save(work);
  api = await serve({ store, callAgent: () => assert.fail('No model call is allowed.'), backend: 'codex', model: null, port: 0, web: resolve(import.meta.dirname, '../web') });
  writeFileSync(join(work.workdir, 'index.html'), `<h1>Delivery with an external widget</h1><iframe title="Third-origin widget" src="${thirdOrigin}/widget.html"></iframe><p id="done">waiting</p><button id="direct">Direct preview probe</button><script>document.querySelector('#direct').onclick = () => (${probe})(${JSON.stringify(api.url + '/api/daemons')});</script>`);
  const snapshots = keep(work, store.dir(work.id), ['index.html']);
  store.append(work.id, { type: 'message', from: 'operator', text: work.request });
  store.append(work.id, { type: 'handoff', role: 'manager', to: 'operator', title: 'Preview isolation', letter: 'A page with an external widget.', open: ['index.html'], snapshots });
  store.save(work);

  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}), args: ['--no-sandbox'] });
  writeFileSync(join(output, 'configuration.json'), JSON.stringify({ api: api.url, thirdOrigin, tokenRequired: false, browser: browser.version() }, null, 2));
  for (const width of [1440, 1024, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    const page = await context.newPage();
    const requests = new Map(), statuses = new Map(), headers = new Map();
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    cdp.on('Network.requestWillBeSent', event => requests.set(event.requestId, event.request));
    cdp.on('Network.requestWillBeSentExtraInfo', event => headers.set(event.requestId, event.headers));
    cdp.on('Network.responseReceivedExtraInfo', event => statuses.set(event.requestId, event.statusCode));
    const before = projectDirectories();
    await page.goto(`${api.url}/?project=${work.id}`);
    await page.locator('.result-card-open').last().click();
    const frame = await page.locator('iframe[title="HTML preview: index.html"]').elementHandle().then(element => element.contentFrame());
    await frame.locator('#direct').waitFor();
    const preview = new URL(frame.url());
    const child = await frame.locator('iframe[title="Third-origin widget"]').elementHandle().then(element => element.contentFrame());
    assert.ok(child, 'The delivered page did not embed its third-origin widget.');
    await child.locator('#done').getByText('finished', { exact: true }).waitFor();
    const afterThird = projectDirectories();
    await frame.getByRole('button', { name: 'Direct preview probe', exact: true }).click();
    await frame.locator('#done').getByText('finished', { exact: true }).waitFor();
    const posts = [...requests].filter(([, request]) => request.url === api.url + '/api/daemons' && request.method === 'POST').map(([id, request]) => ({
      url: request.url, body: request.postData, origin: headers.get(id)?.Origin, contentType: headers.get(id)?.['Content-Type'], status: statuses.get(id),
    }));
    const thirdPost = posts.find(post => post.origin === thirdOrigin);
    const directPost = posts.find(post => post.origin === preview.origin);
    assert.ok(thirdPost && directPost, `Missing browser POSTs: ${JSON.stringify(posts)}`);
    const afterDirect = projectDirectories();
    const row = { width, before, afterThird, afterDirect, third: thirdPost, direct: directPost };
    results.push(row);
    await page.screenshot({ path: join(output, `preview-${width}.png`) });
    writeFileSync(join(output, `network-${width}.json`), JSON.stringify(posts, null, 2));
    assert.deepEqual({ third: thirdPost.status, direct: directPost.status, afterThird: afterThird.length, afterDirect: afterDirect.length }, { third: 403, direct: 403, afterThird: 1, afterDirect: 1 });
    assert.deepEqual(afterThird, before); assert.deepEqual(afterDirect, before);
    assert.equal(thirdPost.contentType, 'text/plain'); assert.equal(thirdPost.body, '{}');
    await context.close();
  }
  console.log('Passed third-origin iframe and direct-preview POST rejection at 1440, 1024 and 390px; one durable project throughout.');
} finally {
  writeFileSync(join(output, 'results.json'), JSON.stringify(results, null, 2));
  await browser?.close();
  await api?.close();
  third.closeAllConnections();
  await new Promise(accept => third.close(accept));
  release();
}
