// Real, explicitly requested Windows desktop acceptance. No mocked IPC/model.
// Uses a separate preview profile; never installs/publishes or edits CLI accounts.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { PREVIEW_DATA_DIRECTORY } from './preview-notes.mjs';

const args = process.argv.slice(2);
const option = name => { const i = args.indexOf(name); return i < 0 ? '' : args[i + 1] || ''; };
for (const key of ['--binary', '--pi', '--dsh', '--output']) assert(option(key), `Missing ${key}`);
assert(process.platform === 'win32', 'Windows desktop acceptance only');
const binary = resolve(option('--binary'));
const stage = dirname(binary);
const output = resolve(option('--output'));
assert(!fs.existsSync(output), 'Evidence output already exists; do not overwrite it.');
fs.mkdirSync(output, { recursive: true });
const manifest = JSON.parse(fs.readFileSync(join(stage, 'argus-backend/_internal/argus/release_manifest.json'), 'utf8'));
const profile = join(process.env.APPDATA, PREVIEW_DATA_DIRECTORY);
const marker = join(profile, 'local-acceptance-owner.json');
const freshProfile = !fs.existsSync(profile);
if (!freshProfile) {
  assert(args.includes('--resume-owned-profile') && fs.existsSync(marker), 'Existing preview data is preserved; use a fresh profile or explicitly resume this acceptance profile.');
} else {
  fs.mkdirSync(profile, { recursive: true });
  fs.writeFileSync(marker, JSON.stringify({ scope: 'local desktop acceptance', created: new Date().toISOString() }), { flag: 'wx' });
}
const sharedConfig = join(profile, 'argus-home', 'config.json');
if (freshProfile) {
  fs.mkdirSync(dirname(sharedConfig), { recursive: true });
  fs.writeFileSync(sharedConfig, JSON.stringify({
    ARGUS_SKILL_RUNNER_BACKEND: 'copilot', ARGUS_SKILL_RUNNER_BIN: 'C:/obsolete/copilot.cmd',
    ARGUS_SKILL_MODEL: 'obsolete-copilot-model', ARGUS_SKILL_MANAGER_MODEL: 'obsolete-manager-model',
  }), { flag: 'wx' });
}
const configBefore = fs.existsSync(sharedConfig) ? createHash('sha256').update(fs.readFileSync(sharedConfig)).digest('hex') : null;
const freePort = async () => {
  const server = createServer(); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const port = server.address().port; await new Promise(done => server.close(done)); return port;
};
const port = await freePort();
const debugPort = await freePort();
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !/^ARGUS_|^PYTHONPATH$|^PYTHONHOME$|^WEBVIEW2_/i.test(key)));
Object.assign(env, {
  ARGUS_DESKTOP_TEST_INSTANCE: randomUUID().replaceAll('-', ''),
  ARGUS_DESKTOP_DISABLE_UPDATE_CHECK: '1',
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort} --remote-debugging-address=127.0.0.1`,
  PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
});
const checks = [];
const record = value => { checks.push(value); console.log(JSON.stringify(value)); };
const child = spawn(binary, [], { cwd: stage, env, stdio: 'ignore', windowsHide: false });
let browser, page, apiBase, credential, sid, failure;
const invoke = (name, input = {}) => page.evaluate(({ name, input }) => window.__TAURI_INTERNALS__.invoke(name, input), { name, input });
async function api(path, body, method = body === undefined ? 'GET' : 'POST', timeout = 240000) {
  const response = await fetch(`${apiBase}${path}`, { method, signal: AbortSignal.timeout(timeout),
    headers: { Authorization: `Bearer ${credential}`, 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) throw new Error(`Acceptance API ${method} ${path}: HTTP ${response.status}`);
  return response.json();
}
async function message(project, text, route_override = 'chat') {
  return api(`/api/projects/${project}/message`, { text, route_override, request_id: randomUUID(),
    ...(route_override === 'task' ? { launch: { hold: false, deliverable: 'report', target_level: 'exploratory' } } : {}),
  });
}
async function createProject(name, workdir) {
  fs.mkdirSync(workdir, { recursive: true });
  const result = await api('/api/daemons', { objective: '', name, workdir, command_id: randomUUID() });
  assert(result.sid && result.rc === 0, 'Could not create acceptance-only project');
  return result.sid;
}
try {
  for (let n = 0; n < 120; n++) {
    assert(child.exitCode === null, 'Preview exited before WebView2 became available');
    try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`, { timeout: 700, noDefaults: true }); break; }
    catch { await delay(300); }
  }
  assert(browser, 'Actual preview WebView2 was not available');
  for (let n = 0; n < 100; n++) {
    page = browser.contexts()[0].pages().find(p => /tauri\.localhost|^tauri:/.test(p.url()));
    if (page) break; await delay(200);
  }
  assert(page, 'Actual native desktop shell not found');
  page.on('dialog', dialog => dialog.accept());
  await page.waitForFunction(() => Boolean(window.__TAURI_INTERNALS__));
  assert.equal(await page.locator('#trialOpen, #trialKey, #trialShortcut').count(), 0);
  const initial = await invoke('get_setup');
  assert.equal(initial.releaseIdentity.releaseId, manifest.release_id);
  assert.equal(initial.releaseIdentity.packageVersion, '0.1.10');
  assert.equal(initial.releaseIdentity.distribution, 'preview');
  await page.screenshot({ path: join(output, 'first-launch.png') });
  record({ check: 'actual preview identity and desktop-only onboarding', ok: true, release_id: manifest.release_id });

  for (const [index, backend] of ['pi', 'dsh', 'pi'].entries()) {
    const started = Date.now();
    if (!await page.locator('#wizard').isVisible()) {
      await page.locator('#fileMenuTrigger').click();
      await page.locator('[data-menu-action="settings"]').click();
    }
    await page.locator('#wizard').waitFor({ state: 'visible', timeout: 30000 });
    const before = await invoke('get_setup');
    const discovered = before.runnerBins[backend] || before.detectedRunners[backend];
    assert(discovered && resolve(discovered).toLowerCase() === resolve(option(`--${backend}`)).toLowerCase(),
      `${backend} detected path differs from the explicitly approved acceptance CLI`);
    await page.locator(`[data-kind="${backend}"]`).click();
    await page.locator('#wizardNext').click();
    await page.locator('#portInput').fill(String(port));
    await page.locator('#wizardNext').click();
    await page.locator('#wizardFinish').click();
    const setupDeadline = Date.now() + 180000;
    while (await page.locator('#wizard').isVisible()) {
      if (await page.locator('#wizardError').isVisible()) throw new Error(await page.locator('#wizardError').textContent());
      assert(Date.now() < setupDeadline, `${backend} native setup timed out`);
      await delay(300);
    }
    const selected = await invoke('get_setup');
    assert.equal(selected.runnerKind, backend);
    const url = new URL(await invoke('open_cockpit'));
    credential = url.searchParams.get('token'); apiBase = url.origin;
    assert(credential, 'Authenticated cockpit URL missing');
    const meta = await api('/api/meta');
    assert.equal(meta.runtime.release_id, manifest.release_id);
    if (!sid) sid = await createProject('Pi → DSH → Pi 成品验收', join(output, 'switch-workspace'));
    const config = await api(`/api/projects/${sid}/config`);
    assert.equal(config.trial_mode, false);
    assert(config.roles.every(role => role.backend === backend), 'Role backend differs from native selection');
    const expected = `ARGUS_PACKAGED_${backend.toUpperCase()}_${index + 1}`;
    const result = await message(sid, `只回复 ${expected}，不要使用工具或修改任何文件。`);
    assert(result.success !== false && String(result.reply || '').trim() === expected, `${backend} did not return the requested real reply`);
    const snapshot = await api(`/api/projects/${sid}/snapshot`);
    assert.equal(snapshot.last_call?.backend, backend);
    record({ check: 'native selection/switch and real Manager reply', backend, index: index + 1,
      ok: true, reply: expected, sid, model: snapshot.last_call?.model || 'CLI native default', elapsed_ms: Date.now() - started });
    await page.screenshot({ path: join(output, `switch-${index + 1}-${backend}.png`) });
  }

  if (configBefore) {
    assert.equal(createHash('sha256').update(fs.readFileSync(sharedConfig)).digest('hex'), configBefore,
      'Desktop switching rewrote the existing shared configuration');
    record({ check: 'legacy shared backend/model configuration preserved', ok: true });
  }

  if (args.includes('--survey')) {
    const workdir = join(output, 'survey-workspace');
    const survey = await createProject('AI 硬件加速器 LaTeX/PDF 成品验收', workdir);
    const question = await message(survey, '我要查找当前主流 AI 加速器，用 LaTeX 写 PDF 综述。先通过 Argus 交接工具向我询问选择硬件还是软件，给出两个选项；确认前不要检索或制作。');
    assert(question.success !== false && /硬件/.test(question.reply) && /软件/.test(question.reply), 'Scope question failed');
    record({ check: 'real scope question', ok: true, sid: survey });
    const dispatch = await message(survey, '选择硬件，确认开始。请交给团队实际执行：在当前独立验收工作区交付 survey.pdf 和 survey.tex，撰写约 6–10 页中文主流 AI 硬件加速器综述，涵盖 NVIDIA、AMD、Google TPU、国产与端侧代表。实际联网查阅至少 6 个官方来源并引用 URL，注明检索日期；区分产品供货与路线图，以及精度、稀疏/稠密、芯片/系统口径。使用已安装的 XeLaTeX 实际编译，保留编译日志并检查 PDF 可读性和引用；执行独立审查。不要安装任何软件或依赖，不读取凭据文件，不操作其他项目或账号。只做有限综述，不开展原创实验、训练或投稿流程。遇到环境问题请具体报告，不能用提纲或假 PDF 冒充完成。', 'task');
    assert(dispatch.success !== false && dispatch.kind !== 'error', 'Survey dispatch failed');
    record({ check: 'real survey dispatch returned', sid: survey, kind: dispatch.kind });
    const deadline = Date.now() + 20 * 60 * 1000;
    let completed = false;
    while (Date.now() < deadline) {
      await delay(10000);
      const snapshot = await api(`/api/projects/${survey}/snapshot`);
      const items = snapshot.backlog || [];
      const statuses = items.map(item => ({ id: item.id, status: item.status }));
      console.log(JSON.stringify({ check: 'survey progress', tasks: statuses }));
      if (items.length && items.every(item => item.status === 'done')) { completed = true; break; }
      if (items.some(item => ['error', 'blocked', 'operator', 'paused'].includes(item.status))) break;
    }
    assert(completed, 'Real survey did not reach successful completion; inspect preserved preview task records');
    const pdf = fs.readFileSync(join(workdir, 'survey.pdf'));
    assert(pdf.subarray(0, 5).toString() === '%PDF-' && pdf.length > 5000, 'Real PDF missing');
    assert(fs.statSync(join(workdir, 'survey.tex')).size > 1000, 'LaTeX source missing');
    record({ check: 'real multi-role research and compiled PDF', ok: true, sid: survey,
      pdf: 'survey-workspace/survey.pdf', sha256: createHash('sha256').update(pdf).digest('hex'), bytes: pdf.length });
    assert(/^[A-Za-z0-9_-]+$/.test(survey), 'Invalid acceptance session id');
    const events = fs.readFileSync(join(profile, 'argus-home', 'projects', survey, 'events.jsonl'), 'utf8')
      .trim().split('\n').map(line => JSON.parse(line));
    const calls = events.filter(event => event.type === 'agent.io.complete');
    assert(!calls.some(event => /Model "(?:inherit|auto|default)" not found|Argus hand_off rejected/.test(event.fatal_error || '')),
      'An automatic model alias or action protocol failure escaped the completed task');
    assert(calls.some(event => event.run_label === 'observer' && event.exit_code === 0),
      'The real Observer call was not successfully exercised');
    record({ check: 'real Observer model inheritance and action protocol', ok: true, sid: survey });
  }
} catch (error) {
  failure = String(error.message || error);
  if (credential) failure = failure.replaceAll(credential, '***');
  console.error(failure);
} finally {
  if (page && child.exitCode === null) {
    const exited = once(child, 'exit');
    await invoke('stop_backend_and_quit').catch(() => undefined);
    await Promise.race([exited, delay(20000)]);
  }
  if (child.exitCode === null) {
    // Only the process tree this script created, never a name-based kill.
    spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
  }
  fs.writeFileSync(join(output, 'acceptance.json'), JSON.stringify({
    release_id: manifest.release_id, version: manifest.package_version, real_desktop: true,
    real_model_calls: true, checks, failure: failure || null,
    preview_profile: profile, user_projects_deleted: false, uploaded: false,
  }, null, 2), { flag: 'wx' });
}
process.exit(failure ? 1 : 0);
