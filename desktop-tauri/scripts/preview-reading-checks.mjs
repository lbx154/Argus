import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from '@playwright/test';

export function createReadingFixture(workspace) {
  mkdirSync(join(workspace, 'src'), { recursive: true });
  writeFileSync(join(workspace, 'src', 'example.py'), `# Argus read-only preview fixture\n${Array.from({ length: 100 }, (_, i) => `value_${i} = ${i}  # readable in both themes`).join('\n')}\n`);
  const stream = (w, h, label) => `q 0.88 0.2 0.2 rg 0 0 18 ${h} re f 0.1 0.5 0.8 rg ${w - 18} 0 18 ${h} re f Q\nBT /F1 22 Tf 45 ${h - 65} Td (Argus PDF - ${label}) Tj /F1 12 Tf 0 -28 Td (Both colored page edges must remain reachable at any zoom.) Tj ET\nq 0.7 G 1 w 40 40 ${w - 80} ${h - 160} re S Q\n`;
  const portrait = stream(600, 900, 'portrait');
  const landscape = stream(900, 600, 'landscape');
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 900] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>',
    `<< /Length ${Buffer.byteLength(portrait)} >>\nstream\n${portrait}endstream`,
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 900 600] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>',
    `<< /Length ${Buffer.byteLength(landscape)} >>\nstream\n${landscape}endstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, i) => { offsets.push(Buffer.byteLength(pdf)); pdf += `${i + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 ${offsets.length}\n0000000000 65535 f \n${offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  writeFileSync(join(workspace, 'preview.pdf'), pdf);
}

async function pdfReady(viewer) {
  await expect(viewer).toHaveAttribute('aria-busy', 'false', { timeout: 30_000 });
  await expect.poll(() => viewer.locator('canvas').evaluate((canvas) => canvas.style.width)).not.toBe('');
}

async function assertReachableEdges(viewer) {
  await pdfReady(viewer);
  const edges = await viewer.locator('.pdf-scroll-viewport').evaluate((scroll) => {
    const canvas = scroll.querySelector('canvas');
    scroll.scrollTo(0, 0);
    const viewport = scroll.getBoundingClientRect();
    const start = canvas.getBoundingClientRect();
    scroll.scrollTo(scroll.scrollWidth, scroll.scrollHeight);
    const end = canvas.getBoundingClientRect();
    return {
      overflows: scroll.scrollWidth > scroll.clientWidth,
      left: start.left - viewport.left,
      top: start.top - viewport.top,
      right: viewport.right - end.right,
      bottom: viewport.bottom - end.bottom,
    };
  });
  assert(edges.overflows, 'Fixture must exercise real horizontal overflow, not just a small PDF.');
  for (const edge of ['left', 'top', 'right', 'bottom']) assert(edges[edge] >= -1, `PDF ${edge} edge is outside reachable scrolling space.`);
}

export async function verifyReadingExperience(page, frame, stage, record) {
  const conversation = frame.locator('.conversation-composer');
  const input = conversation.locator('textarea');
  await expect(input).toBeVisible();
  await expect(conversation.locator('.map-composer-brand [data-logo="rounded-mark"]')).toHaveCount(1);
  await input.fill('/hel');
  await input.press('Enter');
  await expect(input).toHaveValue('/help');
  await input.fill('Draft retained across views');
  await conversation.locator('input[type="file"]').setInputFiles({ name: 'note.txt', mimeType: 'text/plain', buffer: Buffer.from('fixture attachment') });
  await expect(conversation).toContainText('note.txt');
  // Read-only map navigation validates shared state without a provider call.
  await input.focus();
  await page.keyboard.press('Control+.');
  await frame.locator('.workspace-tab').nth(3).click();
  await frame.locator('.workspace-tab').nth(1).click();
  await page.keyboard.press('Control+.');
  await expect(input).toHaveValue('Draft retained across views');
  await expect(conversation).toContainText('note.txt');
  await conversation.getByRole('button', { name: /note\.txt/ }).click();
  await input.fill('');
  record('Shared map-style conversation input retains drafts/attachments across view changes; slash completion works');

  const right = frame.locator('[data-resizable-panel="right"]');
  const selector = right.locator('select');
  await expect(selector.locator('option[value="preview.pdf"]')).toHaveCount(1, { timeout: 20_000 });
  await selector.selectOption('__argus_live_progress__');
  const initialWidth = (await right.boundingBox()).width;
  assert.equal(Math.round(initialWidth), 440, 'A passive delivery refresh must not widen the preview.');
  await selector.selectOption('preview.pdf');
  await expect(frame.locator('aside[role="status"]')).toHaveCount(0);
  await expect.poll(async () => (await right.boundingBox()).width).toBeGreaterThan(initialWidth + 20);
  const pdf = right.locator('.pdf-viewer');
  await pdfReady(pdf);
  for (let i = 0; i < 6; i++) await pdf.getByRole('button', { name: /^(放大|Zoom in)$/ }).click();
  await assertReachableEdges(pdf);
  await pdf.getByRole('button', { name: /下一页|Next/ }).click();
  await assertReachableEdges(pdf);
  await pdf.locator('.pdf-scroll-viewport').evaluate((element) => element.scrollTo(0, 0));
  await page.screenshot({ path: join(stage, 'preview-pdf-zoom.png'), animations: 'disabled' });
  await pdf.getByRole('button', { name: /适合页面|Fit page/ }).click();
  await pdfReady(pdf);
  const fits = await pdf.locator('.pdf-scroll-viewport').evaluate((scroll) => {
    const canvas = scroll.querySelector('canvas');
    return canvas.clientWidth <= scroll.clientWidth && canvas.clientHeight <= scroll.clientHeight;
  });
  assert(fits, 'Fit page must restore a fully visible page.');
  record('PDF portrait/landscape zoom, all four reachable edges, fit-page and wider explicit preview');

  await frame.locator('.workspace-tab').nth(0).click();
  const team = frame.getByRole('region', { name: /AI 研究团队|AI research team/ });
  for (const mode of ['light', 'dark']) {
    const current = await frame.locator('html').getAttribute('data-theme');
    if (current !== mode) await frame.getByRole('button', { name: /theme; switch|主题；切换/ }).click();
    await expect(frame.locator('html')).toHaveAttribute('data-theme', mode);
    const colors = await team.locator('[data-role-dot]').evaluateAll((dots) => dots.map((dot) => getComputedStyle(dot).backgroundColor));
    assert.equal(colors.length, 4);
    assert.equal(new Set(colors).size, 4, `All four role markers must be distinct in ${mode} mode.`);
  }
  record('Four distinct role colors in the real mission UI under both global themes');

  await frame.locator('.workspace-tab').nth(2).click();
  const modules = frame.locator('.workbench-module-tabs button');
  await expect(modules).toHaveCount(3);
  assert.deepEqual(await modules.evaluateAll((buttons) => buttons.map((button) => button.dataset.module)), ['overview', 'experiments', 'ide']);
  await expect(frame.locator('.module-card')).toHaveCount(2);
  await frame.locator('[data-module="ide"]').click();
  const tree = frame.locator('.vscode-sidebar .workspace-tree');
  const folder = tree.getByRole('button', { name: 'src', exact: true });
  const codeFile = tree.getByRole('button', { name: 'example.py', exact: true });
  await expect(codeFile).toBeVisible({ timeout: 20_000 });
  await folder.click();
  await expect(codeFile).toHaveCount(0);
  await frame.getByRole('button', { name: /刷新文件树|Refresh file tree/ }).click();
  await expect(codeFile).toHaveCount(0);
  await folder.click();
  await codeFile.click();
  await expect(frame.locator('.vscode-code code')).toContainText('value_99');
  const code = frame.locator('.vscode-code');
  await expect(code).toBeInViewport();
  await code.evaluate((element) => {
    element.scrollTop = 120;
    element.closest('.ros-content').scrollTop += 120;
  });
  const readingPosition = () => code.evaluate((element) => ({ code: element.scrollTop, module: element.closest('.ros-content').scrollTop }));
  const previousScroll = await readingPosition();
  assert(previousScroll.code > 0 || previousScroll.module > 0, 'Reading-position checks must exercise a genuinely scrolled document.');
  for (const mode of ['light', 'dark']) {
    const current = await frame.locator('html').getAttribute('data-theme');
    if (current !== mode) await frame.getByRole('button', { name: /theme; switch|主题；切换/ }).click();
    await expect(frame.locator('html')).toHaveAttribute('data-theme', mode);
    // Allow only the restrained colour transition; no file reload or remount.
    const brightness = () => code.evaluate((element) => {
      const rgb = getComputedStyle(element).backgroundColor.match(/[\d.]+/g).map(Number);
      return (rgb[0] + rgb[1] + rgb[2]) / 3;
    });
    if (mode === 'light') await expect.poll(brightness).toBeGreaterThan(200);
    else await expect.poll(brightness).toBeLessThan(80);
    assert.deepEqual(await readingPosition(), previousScroll, 'Theme switch lost the file reading position.');
    await page.screenshot({ path: join(stage, `preview-ide-${mode}.png`), animations: 'disabled' });
  }
  await frame.locator('[data-module="overview"]').click();
  await expect(code).toBeHidden();
  await frame.locator('[data-module="ide"]').click();
  await expect(code).toBeVisible();
  assert.deepEqual(await readingPosition(), previousScroll, 'Module switch lost the file reading position.');
  await expect(code.locator('code')).toContainText('value_99');
  record('Three workbench modules, persistent folders and theme-adaptive IDE preserving reading position across tabs');

  let backgroundWorkspaceReads = 0;
  const countWorkspaceReads = (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/v2/workspace/')) backgroundWorkspaceReads++;
  };
  await frame.locator('.workspace-tab').nth(1).click();
  await page.waitForTimeout(250);
  page.on('request', countWorkspaceReads);
  await page.waitForTimeout(8500);
  page.off('request', countWorkspaceReads);
  assert.equal(backgroundWorkspaceReads, 0, 'Hidden IDE must stop its file/tree/Git polling.');
  record('Hidden IDE stops its polling instead of competing with the conversation');
}
