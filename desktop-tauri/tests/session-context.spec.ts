import { expect, test, type FrameLocator, type Page } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { inspectApiMeta } from '../../frontend/core/src/protocol';
import { startSessionFixture, deferred, FIXTURE_AUTH, REPORT_PATH } from './session-context-fixture.mjs';

type Fixture = Awaited<ReturnType<typeof startSessionFixture>>;
let fixture: Fixture;
let frame: FrameLocator;
let errors: string[];
let outside: string[];
const input = () => frame.locator('.conversation-composer textarea');
const projectButton = (letter: string) => frame.locator('.session-card > button').filter({ hasText: `Project ${letter}` }).first();
async function select(letter: string) { await projectButton(letter).click(); await expect(projectButton(letter)).toHaveAttribute('aria-current', 'page'); }
async function notification(page: Page, sessionId: string | undefined, path: string | undefined = REPORT_PATH, deliveryId = `completion:${sessionId || 's-A'}:fixture`) {
  await page.evaluate(payload => (window as unknown as { fixtureOpenDelivery: (p: unknown) => void }).fixtureOpenDelivery(payload), {
    deliveryId, title: 'Synthetic result', summary: 'Fixture only; not review evidence', ...(sessionId ? { sessionId } : {}), ...(path ? { path } : {}),
  });
}
function artifacts() { return fixture.state.trace.filter((row: { endpoint?: string }) => row.endpoint === '/artifact'); }
function writes() { return fixture.state.trace.filter((row: { method: string }) => row.method !== 'GET' && row.method !== 'WS'); }
async function open(page: Page, sid = 's-B') {
  await page.route('**/bridge.ts', route => route.fulfill({ contentType: 'application/javascript', body: `
    window.fixtureNotifications = [];
    export const desktopBridge = new Proxy({
      getStatus: async () => ({ state: 'ready', message: 'Synthetic ready' }),
      getSetup: async () => ({ complete: true, port: 8799, runnerKind: 'copilot', runnerConfigured: true,
        runnerBins: {}, detectedRunners: {}, piConfiguration: { configDir: '' }, releaseIdentity: {}, runtimeIdentity: {} }),
      isWindowVisible: async () => true,
      getAppearance: async () => ({ theme: 'light', resolvedTheme: 'light' }),
      getUpdateStatus: async () => ({ state: 'idle', currentVersion: '0.1.7', userInitiated: false }),
      openCockpit: async () => ${JSON.stringify(fixture.url(sid))},
      onOpenDelivery: callback => { window.fixtureOpenDelivery = callback; return () => {}; },
      notifyDelivery: async payload => { window.fixtureNotifications.push(payload); return true; }
    }, { get: (object, key) => object[key] || (() => undefined) });
  ` }));
  await page.goto('/');
  await expect(page.locator('#splash')).toBeHidden({ timeout: 20_000 });
  frame = page.frameLocator('#cockpitFrame');
  await expect(input()).toBeVisible({ timeout: 20_000 });
}

test.beforeEach(async ({ page, context, baseURL }) => {
  fixture = await startSessionFixture();
  errors = []; outside = [];
  page.on('pageerror', error => errors.push(error.message));
  const allowed = new Set([new URL(baseURL!).origin, fixture.origin]);
  await context.route('**/*', route => {
    const url = route.request().url();
    if (allowed.has(new URL(url).origin)) return route.fallback();
    outside.push(url);
    return route.abort('blockedbyclient');
  });
  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url());
    if (!allowed.has(`${url.protocol === 'wss:' ? 'https:' : 'http:'}//${url.host}`)) outside.push(`${url.protocol}//${url.host}${url.pathname}`);
    socket.close(); // Deterministic fixture: never connects to a real event producer.
  });
});
test.afterEach(async ({ page }, info) => {
  if (info.status !== info.expectedStatus) {
    const diagnostics = JSON.stringify(await Promise.all(page.frames().map(async item => ({
      url: item.url(), text: await item.locator('body').innerText({ timeout: 1000 }).catch(() => '[no body]'),
    }))));
    writeFileSync(info.outputPath('frame-diagnostics.json'), diagnostics);
    await info.attach('frame-diagnostics', { contentType: 'application/json', body: diagnostics });
  }
  await fixture.close();
  await info.attach('request-trace', { body: JSON.stringify(fixture.state.trace, null, 2), contentType: 'application/json' });
  await info.attach('synthetic-root', { body: fixture.root, contentType: 'text/plain' });
  expect(outside).toEqual([]);
  expect(fixture.state.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test('snapshot fixtures use schema 8 without retired snapshot or daemon fields', async () => {
  const meta = await fetch(`${fixture.origin}/api/meta`, {
    headers: { Authorization: `Bearer ${FIXTURE_AUTH}` },
  }).then(response => response.json());
  expect(inspectApiMeta(meta).compatible).toBe(true);
  expect(meta.runtime.package_version).toBe(JSON.parse(
    readFileSync(new URL('../../packages/core/package.json', import.meta.url), 'utf8'),
  ).version);
  expect(Object.keys(meta.runtime).sort()).toEqual(['package_version', 'source_root', 'pid', 'executable', 'started_at'].sort());
  for (const sid of ['s-A', 's-B']) {
    const response = await fetch(`${fixture.origin}/api/projects/${sid}/snapshot`, {
      headers: { Authorization: `Bearer ${FIXTURE_AUTH}` },
    });
    expect(response.status).toBe(200);
    const snapshot = await response.json();
    expect(snapshot.schema_version).toBe(8);
    for (const field of ['recent_events', 'daemon_commands', 'observability', 'mission_view', 'partial', 'diagnostics',
      'global_spend_usd', 'global_spend_status', 'global_usage_summary', 'daemon_admission', 'continuous', 'storage',
      'request_usage', 'cost_control']) {
      expect(Object.hasOwn(snapshot, field), field).toBe(false);
    }
    for (const field of ['read_status', 'read_error', 'protocol_compatible', 'protocol_error',
      'liveness_source', 'heartbeat_age_seconds', 'health', 'mission_width', 'protocol', 'capabilities', 'runtime', 'global_daily_cap_usd']) {
      expect(Object.hasOwn(snapshot.daemon, field), field).toBe(false);
    }
  }
});

test('A1: A/B drafts and File attachments stay isolated across view switches and refresh', async ({ page }) => {
  // Current drafts upload on selection, not on Send; that one A upload is
  // allowed, but switching views or IME input must never submit a message.
  fixture.state.allowWrites.add('s-A:/attachments');
  await open(page, 's-A');
  await input().fill('A draft');
  await frame.locator('.conversation-composer input[type=file]').setInputFiles({ name: 'report.md', mimeType: 'text/markdown', buffer: Buffer.from('A attachment') });
  await select('B');
  await expect(input()).toHaveValue('');
  await expect(frame.locator('.conversation-composer .map-attachment-tray')).toHaveCount(0);
  await input().fill('B draft');
  await select('A');
  await expect(input()).toHaveValue('A draft');
  await expect(frame.locator('.conversation-composer .map-attachment-tray')).toContainText('report.md');
  await frame.locator('.workspace-tabs').getByRole('button', { name: /^(Map|地图)$/ }).click();
  // MapPanel no longer has a second composer; returning to Conversation must
  // preserve the same project-owned draft rather than remount a blank one.
  await expect(frame.locator('textarea').filter({ visible: true })).toHaveCount(0);
  await frame.locator('.workspace-tabs').getByRole('button', { name: /^(Conversation|对话|Activity|动态)$/ }).click();
  await expect(input()).toHaveValue('A draft');
  await input().fill('after map edit');
  await input().press('Shift+Enter');
  await expect(input()).toHaveValue('after map edit\n');
  await input().dispatchEvent('keydown', { key: 'Enter', isComposing: true, keyCode: 229 });
  await expect.poll(() => writes().length).toBe(1);
  expect(writes().map((row: { sid: string; endpoint: string }) => [row.sid, row.endpoint])).toEqual([['s-A', '/attachments']]);
  await page.reload();
  await expect(input()).toBeVisible({ timeout: 20_000 });
  await expect(input()).toHaveValue('after map edit\n'); // Current drafts persist locally by sid.
  await select('B'); await expect(input()).toHaveValue('B draft');
});

test('A1: an A upload cannot submit to B, recover into B or cancel a backend on ordinary selection', async ({ page }) => {
  const gate = deferred();
  fixture.state.delays.set('s-A:/attachments', gate.promise);
  fixture.state.allowWrites.add('s-A:/attachments');
  await open(page, 's-A');
  await input().fill('A uploading');
  await frame.locator('.conversation-composer input[type=file]').setInputFiles({ name: 'report.md', mimeType: 'text/markdown', buffer: Buffer.from('A attachment') });
  await input().press('Enter');
  await expect.poll(() => writes().length).toBe(1);
  await select('B');
  await expect(input()).toHaveValue('');
  await input().fill('B stays');
  gate.resolve();
  await expect(input()).toHaveValue('B stays');
  await select('A');
  await expect(input()).toHaveValue('A uploading');
  expect(writes().map((row: { sid: string; endpoint: string }) => [row.sid, row.endpoint])).toEqual([['s-A', '/attachments']]);
});

test('A1: retyping the same text during upload keeps the new revision', async ({ page }) => {
  const gate = deferred();
  fixture.state.delays.set('s-A:/attachments', gate.promise);
  for (const endpoint of ['/attachments', '/message/stream']) fixture.state.allowWrites.add(`s-A:${endpoint}`);
  fixture.state.streamMode = 'done';
  await open(page, 's-A');
  await input().fill('same text');
  await frame.locator('.conversation-composer input[type=file]').setInputFiles({ name: 'report.md', mimeType: 'text/markdown', buffer: Buffer.from('original A file') });
  await input().press('Enter');
  await expect.poll(() => writes().length).toBe(1);
  await input().fill(''); await input().fill('same text');
  gate.resolve();
  await expect.poll(() => writes().filter((row: { endpoint: string }) => row.endpoint === '/message/stream').length).toBe(1);
  await expect(input()).toHaveValue('same text');
  const upload = writes().find((row: { endpoint: string }) => row.endpoint === '/attachments');
  expect(upload.files).toEqual([{ name: 'report.md', text: 'original A file' }]);
  const sent = writes().find((row: { endpoint: string }) => row.endpoint === '/message/stream');
  expect(sent.sid).toBe('s-A'); expect(sent.body.request_id).toMatch(/^[A-Za-z0-9_-]{1,128}$/);
});

test('A1: pending rewrites disable owner input and never replace another project draft', async ({ page }) => {
  const gate = deferred();
  fixture.state.delays.set('s-A:/prompt/rewrite', gate.promise);
  fixture.state.allowWrites.add('s-A:/prompt/rewrite');
  await open(page, 's-A');
  await input().fill('same text');
  await frame.locator('.conversation-composer').getByRole('button', { name: /rewrite text|改写文字/i }).click();
  await expect.poll(() => writes().length).toBe(1);
  // ChatBox deliberately disables its own input during rewriting. A user
  // cannot create the old same-text revision race through this UI anymore.
  await expect(input()).toBeDisabled();
  await select('B'); await input().fill('B draft');
  await expect(frame.locator('.conversation-composer').getByRole('button', { name: /rewrite text|改写文字/i })).toBeEnabled();
  gate.resolve();
  await expect(input()).toHaveValue('B draft');
  await select('A'); await expect(input()).toHaveValue('s-A synthetic rewrite');
  expect(writes().map((row: { sid: string; endpoint: string }) => [row.sid, row.endpoint])).toEqual([['s-A', '/prompt/rewrite']]);
});

test('A1: stopping an old upload preserves a newly attached same-named File', async ({ page }) => {
  const gate = deferred(); fixture.state.delays.set('s-A:/attachments', gate.promise);
  for (const endpoint of ['/attachments', '/message/stream']) fixture.state.allowWrites.add(`s-A:${endpoint}`);
  fixture.state.streamMode = 'done';
  await open(page, 's-A');
  await input().fill('old draft');
  const files = frame.locator('.conversation-composer input[type=file]');
  await files.setInputFiles({ name: 'report.md', mimeType: 'text/markdown', buffer: Buffer.from('old A attachment') });
  await input().press('Enter');
  await expect.poll(() => writes().length).toBe(1);
  await frame.locator('[data-stop-work]').first().click();
  await frame.locator('[data-stop-confirm]').click();
  expect(writes().map((row: { endpoint: string }) => row.endpoint)).toEqual(['/attachments']);
  // The existing composer forbids new attachments while its local upload
  // submit is still unwinding. Remount via ordinary selection, then attach a
  // genuinely new File while the previous operation's response is still held.
  await select('B'); await select('A');
  await frame.locator('.conversation-composer').getByRole('button', { name: /remove.*report\.md|移除.*report\.md/i }).click();
  await files.setInputFiles({ name: 'report.md', mimeType: 'text/markdown', buffer: Buffer.from('replacement A attachment') });
  await expect(frame.locator('.conversation-composer .map-attachment-tray')).toContainText('report.md');
  await input().fill('new draft'); gate.resolve();
  await expect(input()).toHaveValue('new draft');
  await expect(frame.locator('.conversation-composer .map-attachment-tray')).toContainText('report.md');
  await input().press('Enter');
  await expect.poll(() => writes().filter((row: { endpoint: string }) => row.endpoint === '/message/stream').length).toBe(1);
  expect(writes().filter((row: { endpoint: string }) => row.endpoint === '/attachments').at(-1).files).toEqual([{ name: 'report.md', text: 'replacement A attachment' }]);
  const send = writes().find((row: { endpoint: string }) => row.endpoint === '/message/stream');
  expect(send.body.text).toBe('new draft');
});

test('A1: a stream failure after acceptance does not automatically resend', async ({ page }) => {
  fixture.state.allowWrites.add('s-A:/message/stream'); fixture.state.streamMode = 'error';
  await open(page, 's-A'); await input().fill('send once'); await input().press('Enter');
  await expect(frame.getByRole('alert')).toBeVisible();
  await expect(input()).toHaveValue('send once');
  await expect(frame.locator('.conversation-composer .map-send.is-pending')).toHaveCount(0);
  expect(writes().map((row: { sid: string; endpoint: string }) => [row.sid, row.endpoint])).toEqual([['s-A', '/message/stream']]);
});

test('A2: clicking A from B opens the actual A report URL/content, preserving the B draft and background request', async ({ page }) => {
  fixture.state.snapshots.get('s-B').manager_requests = [{ request_id: 'B-background', status: 'running' }];
  await open(page);
  await input().fill('B unsent');
  await notification(page, 's-A');
  await expect(projectButton('A')).toHaveAttribute('aria-current', 'page');
  await expect(frame.getByRole('heading', { name: 'A ONLY', exact: true })).toBeVisible();
  await expect(frame.getByText('Synthetic report A.', { exact: true })).toBeVisible();
  expect(artifacts().length).toBeGreaterThan(0);
  expect(artifacts().every((row: { sid: string; path: string }) => row.sid === 's-A' && new URL(row.path, fixture.origin).searchParams.get('path') === REPORT_PATH)).toBe(true);
  await expect(frame.getByRole('heading', { name: 'B ONLY', exact: true })).toHaveCount(0);
  expect(writes()).toEqual([]);
  // A listing that is still hydrating uses the existing direct preview modal.
  // Close it as an operator would before selecting another project.
  const dialog = frame.getByRole('dialog');
  if (await dialog.isVisible()) await dialog.getByRole('button', { name: /close|关闭/i }).first().click();
  await select('B'); await expect(input()).toHaveValue('B unsent');
});

test('A2: an available target missing from a stale listing opens directly, never a default file', async ({ page }) => {
  fixture.state.unlisted.add('s-A');
  await open(page); await notification(page, 's-A');
  await expect(projectButton('A')).toHaveAttribute('aria-current', 'page');
  await expect(frame.getByRole('dialog').getByRole('heading', { name: 'A ONLY', exact: true })).toBeVisible();
  expect(artifacts().length).toBeGreaterThan(0);
  expect(artifacts().every((row: { sid: string }) => row.sid === 's-A')).toBe(true);
});

test('A2: waits for a valid delayed snapshot before requesting the target file', async ({ page }) => {
  const gate = deferred(); fixture.state.delays.set('s-A:/snapshot', gate.promise);
  await open(page); await notification(page, 's-A');
  await expect(projectButton('A')).toHaveAttribute('aria-current', 'page');
  expect(artifacts()).toEqual([]); gate.resolve();
  await expect(frame.getByRole('heading', { name: 'A ONLY', exact: true })).toBeVisible();
  expect(artifacts().length).toBeGreaterThan(0);
  expect(artifacts().every((row: { sid: string }) => row.sid === 's-A')).toBe(true);
});

test('A2: a late artifact check cannot reopen A after manual selection of B', async ({ page }) => {
  const gate = deferred(); fixture.state.delays.set('s-A:/artifact', gate.promise);
  await open(page); await input().fill('B draft'); await notification(page, 's-A');
  await expect.poll(() => artifacts().length).toBe(1);
  const aborted = page.waitForEvent('requestfailed', { predicate: request => request.url().includes('/api/projects/s-A/artifact?') });
  await select('B'); await aborted; gate.resolve();
  await expect(input()).toHaveValue('B draft');
  await expect(frame.getByRole('heading', { name: /[AB] ONLY/ })).toHaveCount(0);
  expect(artifacts().every((row: { sid: string }) => row.sid === 's-A')).toBe(true);
});

test('A2: a delayed A snapshot cannot reclaim the UI after a manual B selection', async ({ page }) => {
  const gate = deferred(); fixture.state.delays.set('s-A:/snapshot', gate.promise);
  await open(page);
  await notification(page, 's-A');
  await expect(projectButton('A')).toHaveAttribute('aria-current', 'page');
  expect(artifacts()).toEqual([]);
  await select('B'); gate.resolve();
  await expect(input()).toBeVisible();
  await expect(projectButton('B')).toHaveAttribute('aria-current', 'page');
  expect(artifacts()).toEqual([]);
});

test('A2: the newest of two notification intents wins during target snapshot loading', async ({ page }) => {
  const gate = deferred(); fixture.state.delays.set('s-A:/snapshot', gate.promise);
  await open(page);
  await notification(page, 's-A');
  await expect(projectButton('A')).toHaveAttribute('aria-current', 'page');
  await notification(page, 's-B'); gate.resolve();
  await expect(projectButton('B')).toHaveAttribute('aria-current', 'page');
  await expect(frame.getByRole('heading', { name: 'B ONLY', exact: true })).toBeVisible();
  await expect(frame.getByText('Synthetic report B.', { exact: true })).toBeVisible();
  expect(artifacts().length).toBeGreaterThan(0);
  expect(artifacts().every((row: { sid: string }) => row.sid === 's-B')).toBe(true);
});

for (const condition of ['deleted', 'forbidden', 'missing'] as const) {
  test(`A2: ${condition} A never falls back to B's same-named report`, async ({ page }) => {
    await open(page);
    fixture.state[condition].add('s-A');
    await notification(page, 's-A');
    await expect(frame.locator('[role="alert"], [role="status"]').filter({ hasText: /notification|通知/i }).first()).toBeVisible();
    expect(artifacts().every((row: { sid: string }) => row.sid === 's-A')).toBe(true);
    await expect(frame.getByRole('heading', { name: 'B ONLY', exact: true })).toHaveCount(0);
    expect(writes()).toEqual([]);
  });
}

test('A2: no-path and unknown legacy notifications do not guess a file or project', async ({ page }) => {
  await open(page);
  await notification(page, undefined);
  await expect(frame.getByRole('status').filter({ hasText: /notification|通知/i }).first()).toBeVisible();
  await expect(projectButton('B')).toHaveAttribute('aria-current', 'page');
  expect(artifacts()).toEqual([]);
  await notification(page, 's-A', '');
  await expect(projectButton('A')).toHaveAttribute('aria-current', 'page');
  expect(artifacts()).toEqual([]);
});

test('A2: shell source/origin checks remain intact and legitimate forwarding preserves sessionId', async ({ page }) => {
  await open(page);
  const payload = { deliveryId: 'synthetic-forwarding', title: 'Fixture only', summary: 'Not review evidence', path: REPORT_PATH, sessionId: 's-A' };
  await page.evaluate(({ payload, origin }) => {
    const child = document.querySelector<HTMLIFrameElement>('#cockpitFrame')!.contentWindow;
    for (const [source, senderOrigin] of [[window, origin], [child, 'https://untrusted.invalid']] as const) {
      window.dispatchEvent(new MessageEvent('message', { source, origin: senderOrigin, data: { type: 'argus:notify-completion', payload } }));
    }
  }, { payload, origin: fixture.origin });
  const forwarded = () => page.evaluate(() => (window as unknown as { fixtureNotifications: unknown[] }).fixtureNotifications);
  expect(await forwarded()).toEqual([]);
  const cockpit = page.frames().find(candidate => candidate.url().startsWith(fixture.origin))!;
  await cockpit.evaluate(payload => window.parent.postMessage({ type: 'argus:notify-completion', payload }, '*'), payload);
  await expect.poll(forwarded).toEqual([payload]);
});

test('the current skill library opens feed, shared documents and store without losing a draft or writing', async ({ page }) => {
  await open(page, 's-A');
  await input().fill('Keep while reading knowledge');
  await frame.getByRole('button', { name: 'Skill library', exact: true }).click();
  const library = frame.locator('[data-learning-library]');
  await expect(library).toBeVisible();
  // LearningLibrary replaced wiki's vertical/project/principles tabs. Exercise
  // every current read-only category, not a restored retired wiki UI.
  await library.getByRole('button', { name: /Reviewed research lesson/ }).last().click();
  await expect(library.getByRole('article')).toContainText('Synthetic shared body for Reviewed research lesson.');
  await library.getByRole('button', { name: 'Across projects', exact: true }).click();
  await library.getByRole('searchbox').fill('Shared host');
  await library.getByRole('button', { name: /Shared host reference/ }).click();
  await expect(library.getByRole('article')).toContainText('Synthetic shared body for Shared host reference.');
  const requested = fixture.state.trace.filter((row: { path: string }) => row.path.startsWith('/api/skill-library/document?'));
  expect(requested).toHaveLength(2);
  expect(requested.every((row: { path: string }) => new URL(row.path, fixture.origin).searchParams.get('sid') === 's-A')).toBe(true);
  await library.getByRole('button', { name: 'Skill store', exact: true }).click();
  await expect(library.getByRole('heading', { name: 'Synthetic store pack' })).toBeVisible();
  await expect(library.getByRole('button', { name: 'Install', exact: true })).toBeEnabled();
  await frame.getByRole('dialog').getByRole('button', { name: /close|关闭/i }).first().click();
  await expect(input()).toHaveValue('Keep while reading knowledge');
  expect(writes()).toEqual([]);
});

test('A2: non-parent messages, wrong origins and malicious session identities are refused', async ({ page }) => {
  await open(page);
  const cockpit = page.frames().find(candidate => candidate.url().startsWith(fixture.origin))!;
  await cockpit.evaluate(parentOrigin => {
    if (window.location.ancestorOrigins[0] !== parentOrigin) throw new Error('Fixture is not embedded in its actual shell');
    const payload = { deliveryId: 'untrusted', title: 'x', summary: '', path: 'results/report.md', sessionId: 's-A' };
    for (const [source, origin] of [[window, parentOrigin], [window.parent, 'https://untrusted.invalid']] as const) {
      window.dispatchEvent(new MessageEvent('message', { source, origin, data: { type: 'argus:open-delivery', payload } }));
    }
  }, new URL(page.url()).origin);
  for (const sid of ['../s-A', 's-A/../../s-B', `s-A${'x'.repeat(300)}`, ' s-A', 's-A\u0000']) await notification(page, sid);
  await expect(projectButton('B')).toHaveAttribute('aria-current', 'page');
  expect(artifacts()).toEqual([]);
});
