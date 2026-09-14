import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { inflateSync } from 'node:zlib';
import { expect } from '@playwright/test';

// Decode only the small, trusted, non-interlaced RGB(A) PNG crops produced by
// Chromium. Compare pixels, not PNG metadata or an animated CSS matrix alone.
function pixels(png) {
  assert.equal(png.subarray(1, 4).toString(), 'PNG');
  let width, height, channels;
  const chunks = [];
  for (let offset = 8; offset < png.length;) {
    const size = png.readUInt32BE(offset);
    const type = png.toString('ascii', offset + 4, offset + 8);
    const data = png.subarray(offset + 8, offset + 8 + size);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      assert.equal(data[8], 8);
      assert([2, 6].includes(data[9]));
      assert.equal(data[12], 0);
      channels = data[9] === 6 ? 4 : 3;
      assert(width * height <= 250_000, 'Only small eye crops may be decoded.');
    }
    if (type === 'IDAT') chunks.push(data);
    offset += size + 12;
  }
  const stride = width * channels;
  const raw = inflateSync(Buffer.concat(chunks), { maxOutputLength: (stride + 1) * height });
  assert.equal(raw.length, (stride + 1) * height);
  const decoded = Buffer.alloc(stride * height);
  const paeth = (a, b, c) => {
    const p = a + b - c, x = Math.abs(p - a), y = Math.abs(p - b), z = Math.abs(p - c);
    return x <= y && x <= z ? a : y <= z ? b : c;
  };
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    assert(filter <= 4);
    for (let x = 0; x < stride; x++) {
      const at = y * stride + x;
      const left = x >= channels ? decoded[at - channels] : 0;
      const up = y ? decoded[at - stride] : 0;
      const corner = y && x >= channels ? decoded[at - stride - channels] : 0;
      const predictor = [0, left, up, Math.floor((left + up) / 2), paeth(left, up, corner)][filter];
      decoded[at] = (raw[y * (stride + 1) + x + 1] + predictor) & 255;
    }
  }
  return { width, height, channels, data: decoded };
}

function changedPixels(a, b) {
  assert.equal(a.width, b.width); assert.equal(a.height, b.height); assert.equal(a.channels, b.channels);
  let changed = 0;
  for (let i = 0; i < a.data.length; i += a.channels) {
    if ([0, 1, 2].some(channel => Math.abs(a.data[i + channel] - b.data[i + channel]) > 20)) changed++;
  }
  return changed;
}

export async function verifyEyeMotion(page, selector, {
  directory, label, duration = 1100, moving, minimumExcursion = 0.04,
} = {}) {
  const eye = page.locator(selector);
  await expect(eye).toBeVisible();
  // Do not finish/cancel animations or override media for the native baseline.
  // Wait only for finite parent entrance effects, so they cannot mimic eye motion.
  await eye.evaluate(async element => {
    const entrance = [];
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      for (const animation of parent.getAnimations()) {
        if (animation.effect?.getComputedTiming().iterations !== Infinity) entrance.push(animation.finished.catch(() => {}));
      }
    }
    await Promise.all(entrance);
  });
  const media = await page.evaluate(() => {
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const eyeMode = document.documentElement.dataset.startupEyeMotion || 'system';
    return { reducedMotion, eyeMode, motionEnabled: eyeMode === 'on' || (eyeMode === 'system' && !reducedMotion) };
  });
  const shouldMove = moving ?? media.motionEnabled;
  const clip = await eye.evaluate(element => {
    const box = element.ownerSVGElement.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width, height: box.height };
  });
  const geometry = eye.evaluate((element, milliseconds) => new Promise((resolve, reject) => {
    const start = performance.now(), frames = [];
    const timeout = setTimeout(() => reject(new Error('Visible animation frames stopped advancing.')), milliseconds + 4000);
    function sample(time) {
      const circle = element.querySelector('.argus-mark-pupil');
      const highlight = element.querySelector('.argus-mark-highlight');
      const svg = element.ownerSVGElement;
      const box = svg.getBoundingClientRect();
      const matrix = circle.getScreenCTM();
      const highlightMatrix = highlight?.getScreenCTM();
      if (matrix && highlightMatrix && box.width) {
        const point = new DOMPoint(circle.cx.baseVal.value, circle.cy.baseVal.value).matrixTransform(matrix);
        const bright = new DOMPoint(highlight.cx.baseVal.value, highlight.cy.baseVal.value).matrixTransform(highlightMatrix);
        const hit = document.elementFromPoint(point.x, point.y);
        const highlightHit = document.elementFromPoint(bright.x, bright.y);
        const scale = Math.hypot(matrix.a, matrix.b) / box.width;
        const highlightScale = Math.hypot(highlightMatrix.a, highlightMatrix.b) / box.width;
        frames.push({ time: time - start, x: (point.x - box.x) / box.width,
          y: (point.y - box.y) / box.height,
          highlightX: (bright.x - box.x) / box.width,
          highlightY: (bright.y - box.y) / box.height,
          relativeX: (bright.x - point.x) / box.width,
          relativeY: (bright.y - point.y) / box.width,
          pupilRadius: circle.r.baseVal.value * scale,
          highlightRadius: highlight.r.baseVal.value * highlightScale,
          visible: element.contains(hit) && element.contains(highlightHit)
            && Number(getComputedStyle(highlight).opacity) > 0 });
      }
      if (time - start >= milliseconds) { clearTimeout(timeout); resolve(frames); }
      else requestAnimationFrame(sample);
    }
    requestAnimationFrame(sample);
  }), duration);
  const unobscured = () => eye.evaluate(element => {
    const circle = element.querySelector('.argus-mark-pupil');
    const matrix = circle.getScreenCTM();
    if (!matrix) return false;
    const point = new DOMPoint(circle.cx.baseVal.value, circle.cy.baseVal.value).matrixTransform(matrix);
    return element.contains(document.elementFromPoint(point.x, point.y));
  });
  const crops = [];
  for (let index = 0; index < 4; index++) {
    if (index) await delay(duration / 4);
    if (!await unobscured()) continue;
    const png = await page.screenshot({ clip, type: 'png', animations: 'allow' });
    if (!await unobscured()) continue; // A cockpit reveal during capture is not eye motion.
    crops.push(pixels(png));
    if (directory) writeFileSync(join(directory, `${label}-eye-${index}.png`), png, { flag: 'wx' });
  }
  const frames = (await geometry).filter(frame => frame.visible);
  assert(frames.length >= 5, 'The pupil must be visibly exposed, not merely animated behind an overlay.');
  assert(crops.length >= 2, 'At least two actually visible eye crops are required.');
  const excursion = Math.max(...frames.map(frame => frame.x)) - Math.min(...frames.map(frame => frame.x));
  const vertical = Math.max(...frames.map(frame => frame.y)) - Math.min(...frames.map(frame => frame.y));
  const changed = crops.slice(1).map(crop => changedPixels(crops[0], crop));
  const spread = key => Math.max(...frames.map(frame => frame[key])) - Math.min(...frames.map(frame => frame[key]));
  const highlightExcursion = Math.max(spread('highlightX'), spread('highlightY'));
  const highlightRelativeExcursion = Math.max(spread('relativeX'), spread('relativeY'));
  const distances = frames.map(frame => Math.hypot(frame.relativeX, frame.relativeY));
  const minimumHighlightMargin = Math.min(...frames.map((frame, index) =>
    frame.pupilRadius - frame.highlightRadius - distances[index]));
  assert(minimumHighlightMargin > 0, 'The complete highlight must remain inside the black pupil.');
  assert(Math.max(...distances) - Math.min(...distances) < 0.001,
    'Pupil and highlight must share one stable rotation, without independent drifting clocks.');
  const result = { ...media, expectedMoving: shouldMove, visibleFrames: frames.length,
    horizontalExcursion: excursion, verticalExcursion: vertical, highlightExcursion,
    highlightRelativeExcursion, minimumHighlightMargin, changedPixels: changed, frames };
  if (directory) writeFileSync(join(directory, `${label}-eye-evidence.json`), JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
  if (shouldMove) {
    assert(Math.max(excursion, vertical) > minimumExcursion, 'The actual pupil center must orbit, not just its highlight.');
    assert(highlightExcursion > 0.015, 'The actual white highlight must visibly move with the pupil.');
    assert(highlightRelativeExcursion > 0.015, 'Counter-rotation must not pin the highlight inside the moving pupil.');
    assert(Math.max(...changed) >= (clip.width < 50 ? 3 : 20), 'Rendered eye pixels did not visibly change.');
  } else {
    assert(Math.max(excursion, vertical, highlightExcursion, highlightRelativeExcursion) < 0.001,
      'Reduced/paused motion must stop both the pupil and its highlight.');
    assert.equal(Math.max(...changed), 0, 'Reduced/paused eye pixels must stay still.');
  }
  return result;
}

export async function verifyColdStartupTiming(page) {
  await expect(page.locator('#cockpit')).toBeVisible();
  const result = await page.evaluate(() => ({
    start: performance.getEntriesByName('argus:splash-visible')[0]?.startTime,
    shown: performance.getEntriesByName('argus:cockpit-visible')[0]?.startTime,
    reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
    eyeMode: document.documentElement.dataset.startupEyeMotion || 'system',
  }));
  result.motionEnabled = result.eyeMode === 'on' || (result.eyeMode === 'system' && !result.reducedMotion);
  assert(Number.isFinite(result.start) && Number.isFinite(result.shown), 'Natural configured cold-start timing is missing.');
  result.visibleMilliseconds = result.shown - result.start;
  if (result.motionEnabled) assert(result.visibleMilliseconds >= 1030, 'Cold start covered the eye before one visible cycle.');
  return result;
}
