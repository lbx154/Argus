// Additional checks against a NEW packaged desktop; faults are explicitly
// injected in browser reads only, never substituted model replies or IPC.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { join } from 'node:path';
import { cockpitFrame, connectionSnapshot } from './connection-evidence.mjs';

export async function preserveConversation(page, origin, name, sid) {
  const frame = await cockpitFrame(page, origin);
  assert(frame, 'Packaged cockpit missing');
  await frame.locator('.session-card > button').filter({ hasText: name }).first().click({ timeout: 30000 });
  await frame.locator('.workspace-tabs .workspace-tab').first().click();
  const draft = `Unsent recovery acceptance ${randomUUID()}`;
  const file = 'unsent-recovery-attachment.md';
  await frame.locator('.conversation-composer textarea').fill(draft);
  await frame.locator('.conversation-composer input[type=file]').setInputFiles({ name: file, mimeType: 'text/markdown', buffer: Buffer.from('Independent acceptance attachment, not a user document.') });
  const nonce = randomUUID();
  await frame.evaluate(nonce => {
    window.__connectionAcceptance = { nonce, notifications: [] };
    window.addEventListener('message', event => {
      if (event.data?.type === 'argus:backend-ready') window.__connectionAcceptance.notifications.push({ at: Date.now(), generation: event.data.generation });
    });
  }, nonce);
  await delay(1000);
  return { draft, file, nonce, sid };
}

export async function assertConversation(page, origin, expected) {
  const frame = await cockpitFrame(page, origin);
  const document = await frame.evaluate(() => ({ nonce: window.__connectionAcceptance?.nonce,
    sid: new URL(location.href).searchParams.get('project'), notifications: window.__connectionAcceptance?.notifications || [] }));
  assert.equal(document.nonce, expected.nonce, 'Cockpit document was reloaded');
  assert.equal(document.sid, expected.sid, 'Current project was lost');
  assert.equal(await frame.locator('.conversation-composer textarea').inputValue(), expected.draft, 'Unsent draft was lost');
  assert((await frame.locator('.conversation-composer .map-attachment-tray').textContent())?.includes(expected.file), 'Draft attachment was lost');
  return document;
}

async function waitView(page, origin, predicate, description) {
  for (let n = 0; n < 240; n++) {
    const frame = await cockpitFrame(page, origin);
    const view = frame && await connectionSnapshot(frame);
    if (view && predicate(view)) return view;
    await delay(100);
  }
  throw new Error(`Packaged fault check timed out: ${description}`);
}

export async function verifyReadFaults({ page, origin, invoke, record, output, preserved }) {
  const taskPosts = [];
  const watch = request => {
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && /\/(message(?:\/stream)?|backlog|start|nudge|tasks)$/.test(path)) taskPosts.push(path);
  };
  page.on('request', watch);
  const restart = async () => {
    const previous = await invoke('get_status');
    await invoke('restart_backend');
    for (let n = 0; n < 300; n++) {
      const status = await invoke('get_status');
      if (status.state === 'ready' && status.pid && status.pid !== previous.pid) return;
      await delay(100);
    }
    throw new Error('Actual backend restart did not become ready');
  };
  try {
    for (const fault of ['network-loss', 'server-401']) {
      const pattern = '**/api/projects/costs';
      const handler = route => fault === 'network-loss' ? route.abort('connectionfailed')
        : route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer invalid-acceptance-only' } });
      await page.route(pattern, handler);
      try {
        await waitView(page, origin, view => fault === 'network-loss' ? view.unreachable : view.pairing, fault);
        await page.screenshot({ path: join(output, `${fault}-visible.png`) });
        await restart();
        const failed = await waitView(page, origin, view => view.queries.some(q => q.key === 'project-costs' && q.status === 'error' && q.fetchStatus === 'idle')
          && (fault === 'network-loss' ? view.unreachable : view.pairing), `${fault} must remain after ready`);
        record({ check: 'injected read failure remains visible after actual native restart', fault, ok: true, view: failed });
        await assertConversation(page, origin, preserved);
      } finally { await page.unroute(pattern, handler); }
      await restart();
      const recovered = await waitView(page, origin, view => !view.unreachable && !view.pairing && view.queries.length === 2
        && view.queries.every(q => q.status === 'success'), `${fault} recovery`);
      await assertConversation(page, origin, preserved);
      record({ check: 'real authenticated reads recover after removing injection without reload', fault, ok: true, view: recovered });
    }
    assert.equal(taskPosts.length, 0, 'Recovery replayed a task POST');
    record({ check: 'no automatic task POST during fault/recovery; draft, attachment, project and document retained', ok: true, task_posts: taskPosts.length });
    await page.screenshot({ path: join(output, 'connection-recovered.png') });
  } finally { page.off('request', watch); }
}
