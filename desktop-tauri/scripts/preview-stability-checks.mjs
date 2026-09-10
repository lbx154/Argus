import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { expect } from '@playwright/test';

export async function verifyRuntimeStability({ page, frame, stage, dataDir, native, request, reveal, seconds, record }) {
  const initial = await native('get_status');
  const ownershipPath = join(dataDir, 'runtime', 'backend.json');
  const ownership = readFileSync(ownershipPath);
  const assertLive = async () => {
    const status = await native('get_status');
    assert.equal(status.state, 'ready', 'Authenticated backend was incorrectly rejected.');
    assert.equal(status.pid, initial.pid, 'A living backend was unexpectedly restarted.');
    return status;
  };
  // Fault injection is confined to this disposable staged package and fixture
  // AppData. Never modify the operator's installation or credentials.

  try {
    writeFileSync(ownershipPath, '{partial write');
    await delay(11_000);
    await assertLive();
    assert.equal((await request('/api/meta')).status, 200);
  } finally { writeFileSync(ownershipPath, ownership); }
  record('Corrupted on-disk ownership does not invalidate the already verified live session');

  const started = Date.now();
  let probes = 0;
  let hidden = false;
  let wasHidden = false;
  while (Date.now() - started < seconds * 1000) {
    const status = await assertLive();
    assert(!status.warning, 'Runtime warning during steady-state soak.');
    const response = await request('/api/projects');
    assert.equal(response.status, 200);
    if (probes % 12 === 0) {
      // Read-only pages: never enqueue a task or trigger map-summary generation.
      await frame.locator('.workspace-tab').nth(0).click();
      await frame.locator('.workspace-tab').nth(3).click();
      assert.equal((await request('/api/projects/s-preview-smoke/map')).status, 200);
      console.log(`SOAK ${Math.floor((Date.now() - started) / 1000)}s / ${seconds}s, same authenticated PID`);
    }
    if (!wasHidden && probes === 1) { await native('hide_desktop'); hidden = true; wasHidden = true; }
    if (hidden && probes >= 7) { await reveal(); hidden = false; }
    probes++;
    await delay(Math.min(5000, Math.max(0, seconds * 1000 - (Date.now() - started))));
  }
  if (hidden) await reveal();
  await assertLive();
  record(`Steady-state soak ${seconds}s: ${probes} checks, same authenticated PID, read-only page switches and background resume`);
  if (seconds >= 1800) {
    // This PID was authenticated above and belongs to this script's isolated
    // host. Exercise genuine process death only after the stable-PID soak.
    const stopped = spawnSync('taskkill.exe', ['/pid', String(initial.pid), '/t', '/f'], { stdio: 'ignore' });
    assert.equal(stopped.status, 0);
    await expect.poll(async () => {
      const status = await native('get_status');
      return status.state === 'ready' && status.pid !== initial.pid;
    }, { timeout: 60_000 }).toBe(true);
    const recovered = await native('get_status');
    await delay(65_000);
    const stable = await native('get_status');
    assert.equal(stable.state, 'ready');
    assert.equal(stable.pid, recovered.pid);
    assert.equal((await request('/api/meta')).status, 200);
    record('A deliberately terminated test backend recovers under a new verified identity and stays stable past the 60s reset');
  }
}
