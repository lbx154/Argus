// Real cockpit replay at its served entry, at one width; the merge task covers combined widths.
// Run with node --import tsx after core/web builds; pass a fresh output directory.
// Shared browser tools can be supplied via PLAYWRIGHT_MODULE / CHROMIUM_EXECUTABLE.
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { Store } from '../dist/store.js';
import { serve } from '../dist/serve/index.js';
import { plannerBatchReplay } from './replyTurnsReplay.ts';

assert.ok(process.argv[2], 'Pass a fresh evidence directory');
const output = resolve(process.argv[2]);
assert.ok(!existsSync(join(output, 'home')), 'Evidence home must be new');
mkdirSync(output, { recursive: true });
const store = new Store(join(output, 'home'));
const own = store.own();
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const markdown = '正文 `nvidia-smi -i 1` **ECC** [链接](https://example.com) [文件](report.md)\n\n# 一级标题\n\n## 二级标题\n\n### 检查项\n\n| 表头 |\n| --- |\n| 单元格 |\n\n```sh\necho healthy\n```';
const frozen = new Map();
const projects = [];
function copy(record, original, name) {
  const project = store.create({ workdir: join(output, 'work'), request: name, backend: 'codex', model: null });
  Object.assign(project, original, { id: project.id, workdir: project.workdir, name, status: 'stopped', sessions: {} });
  store.save(project);
  const bytes = record.map(event => JSON.stringify(event)).join('\n') + '\n';
  writeFileSync(join(store.dir(project.id), 'events.jsonl'), bytes);
  frozen.set(project.id, bytes); projects.push(project);
  return project;
}
const fixture = new URL('./fixtures/progress-messages/', import.meta.url);
const teamRecord = readFileSync(new URL('events.jsonl', fixture), 'utf8').trim().split('\n').map(line => JSON.parse(line));
const teamOriginal = JSON.parse(readFileSync(new URL('project.json', fixture), 'utf8'));
const teamStart = teamRecord.findIndex(event => event.type === 'call.start' && event.role === 'manager' && !event.answers);
const team = copy([...teamRecord.slice(0, teamStart + 1),
  { type: 'step', role: 'manager', kind: 'thought', text: 'Checking team results', ts: teamRecord[teamStart].ts },
  { type: 'step', role: 'manager', kind: 'message', text: markdown, ts: teamRecord[teamStart].ts },
  { type: 'handoff', role: 'manager', to: 'operator', letter: markdown, ts: teamRecord[teamStart].ts }], teamOriginal, '过程颜色与团队回合');
const batch = await plannerBatchReplay();
const mixed = copy(batch.record, batch.project, '规划者混合来信');
writeFileSync(join(output, 'batch-prompts.json'), JSON.stringify(batch.prompts, null, 2));
// A second entry check drives actual live team work, Stop and Resume through the UI.
const active = store.create({ workdir: join(output, 'work'), request: '', backend: 'codex', model: null });
let managerCalls = 0, teamAttempts = 0, releaseLive;
const liveFinished = new Promise(resolve => { releaseLive = resolve; });
const callAgent = async request => {
  assert.ok(request.session?.startsWith(active.id + '/'), 'Only the live check can call the fixture backend');
  const result = (to, letter) => ({ callId: 'controlled', threadId: 'session', completed: true, error: null,
    handoff: { form: request.form, to, letters: [letter] }, model: 'fixture', usage: null, ms: 1 });
  if (request.role === 'engineer') return result('manager', '团队结果已完成');
  if (++managerCalls === 1) return result('engineer', '核对 a.py');
  const attempt = ++teamAttempts;
  request.onStep({ kind: 'thought', text: 'Checking team results' });
  request.onStep({ kind: 'message', text: `团队过程 ${attempt}` });
  if (attempt === 1) {
    await new Promise(resolve => request.signal.addEventListener('abort', resolve, { once: true }));
    return { ...result('operator', ''), completed: false, handoff: null, error: 'Interrupted' };
  }
  await liveFinished;
  return result('operator', '团队核对完成');
};
let api, browser;
const results = [];
try {
  api = await serve({ store, backend: 'codex', model: null, port: 0, web: resolve(import.meta.dirname, '../../../frontend/web/dist'), callAgent });
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}), args: ['--no-sandbox'] });
  for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({ theme }) => { localStorage.setItem('argus.locale', 'zh-CN'); localStorage.setItem('argus.theme', theme); }, { theme });
    await page.goto(`${api.url}/?project=${team.id}`);
    // Said before a letter to the operator, the words are its process, folded until opened.
    await page.locator('[data-manager-process]').last().waitFor({ state: 'attached' });
    const progress = page.locator('[data-manager-process]').last();
    const measurements = await progress.evaluate(element => {
      const color = node => getComputedStyle(node).color;
      return { body: color(element.querySelector('p')), elements: [...element.querySelectorAll('code,strong,h1,h2,h3,th,td')].map(node => ({ tag: node.tagName, color: color(node) })),
        controls: [...element.querySelectorAll('a,button')].map(node => ({ tag: node.tagName, color: color(node) })) };
    });
    const reply = progress.locator('xpath=../..');
    const answerColor = await reply.locator('p').last().evaluate(node => getComputedStyle(node).color);
    const result = { theme, width: 1440, ...measurements, answerColor, errors };
    results.push(result);
    writeFileSync(join(output, 'colors.json'), JSON.stringify(results, null, 2));
    await page.screenshot({ path: join(output, `team-${theme}.png`) });
    assert.deepEqual(measurements.elements.map(element => element.tag).sort(), ['CODE', 'CODE', 'H1', 'H2', 'H3', 'STRONG', 'TD', 'TH'], 'all requested Markdown elements are actually rendered');
    for (const element of measurements.elements) assert.equal(element.color, measurements.body, `${theme} process ${element.tag} inherits body color`);
    assert.notEqual(answerColor, measurements.body, 'answer remains distinct');
    assert.ok(measurements.controls.length >= 3, 'link, file button and copy button render');
    for (const control of measurements.controls) assert.notEqual(control.color, measurements.body, `${theme} ${control.tag} stays discernible`);
    assert.ok(!(await page.locator('.conversation-row').allTextContents()).join('\n').includes('正在回复你的消息'));
    // Open the existing project role-log entry through the cockpit.
    await page.goto(`${api.url}/?project=${mixed.id}`);
    await page.getByText('我来安排 B 的 b.py 任务。', { exact: true }).waitFor({ state: 'attached' });
    const owner = page.locator('.conversation-row').filter({ hasText: '我来安排 B 的 b.py 任务。' });
    assert.equal(await owner.count(), 1);
    assert.ok((await owner.textContent()).includes('b.py 收到，B 完成。'));
    assert.ok(!(await owner.textContent()).includes('已交给规划者'), 'mixed process is its own row, not a request brief');
    assert.deepEqual(errors, []);
    await page.screenshot({ path: join(output, `batch-${theme}.png`) });
    await context.close();
  }
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem('argus.locale', 'zh-CN'));
  await page.goto(`${api.url}/?project=${active.id}`);
  const composer = page.locator('textarea').last();
  await composer.fill('核对团队回合'); await composer.press('Enter');
  const owner = page.locator('.conversation-row').filter({ hasText: '团队过程 1' });
  await owner.waitFor();
  await page.locator('.topbar-work-status').filter({ hasText: '团队正在工作' }).waitFor();
  const measure = async phase => {
    assert.equal(await owner.count(), 1);
    assert.equal(await owner.locator('[data-manager-reply]').count(), 0, 'non-request row has no request status');
    assert.ok(!(await page.locator('.conversation-row').allTextContents()).join('\n').includes('正在回复你的消息'));
    const log = page.locator('[data-role="manager"] [data-role-thought]');
    if (!phase.startsWith('stopped')) {
      await log.waitFor(); assert.equal(await log.textContent(), '正在处理团队工作');
    }
    await page.screenshot({ path: join(output, `live-${phase}.png`) });
  };
  await measure('running');
  await page.locator('[data-stop-work]').first().click(); await page.locator('[data-stop-confirm]').click();
  await page.locator('.topbar-work-status').filter({ hasText: '已停止' }).waitFor();
  await measure('stopped');
  await page.reload(); await owner.waitFor(); await measure('stopped-reload');
  await page.locator('[data-resume-work]').first().click();
  await owner.getByText('团队过程 2', { exact: true }).waitFor();
  await measure('resumed');
  releaseLive(); await owner.getByText('团队核对完成', { exact: true }).waitFor();
  assert.ok((await owner.textContent()).includes('团队过程 1'));
  assert.ok((await owner.textContent()).includes('团队过程 2'));
  await context.close();
  for (const [id, bytes] of frozen) assert.equal(readFileSync(join(store.dir(id), 'events.jsonl'), 'utf8'), bytes, 'frozen replay unchanged');
  writeFileSync(join(output, 'result.json'), JSON.stringify({ passed: true, browser: browser.version(), cases: results.length, liveStopResumePassed: true, frozenRecordsUnchanged: true }, null, 2));
} finally {
  releaseLive(); await browser?.close(); await api?.close(); own();
}
