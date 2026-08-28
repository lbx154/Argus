import assert from 'node:assert/strict';
import test from 'node:test';
import {
  ARGUS_ROUNDED_ART_COMPACT,
  ARGUS_ROUNDED_ART_FULL,
  splashLogoForWidth,
} from '../src/components/Splash.js';

test('compact Rounded art has the eye glyph and is four lines', () => {
  const compact = ARGUS_ROUNDED_ART_COMPACT;
  assert.equal(compact.length, 4);
  assert.match(compact.join('\n'), /[●◉]/);
});

test('full Rounded art has the eye glyph, no legacy banner, and is six lines', () => {
  const full = ARGUS_ROUNDED_ART_FULL;
  assert.equal(full.length, 6);
  assert.doesNotMatch(full.join('\n'), /ARGUS-SKILL/);
  assert.match(full.join('\n'), /[●◉]/);
  assert.match(full.join('\n'), /Autonomous Work Lab/);
  assert.ok(Math.max(...full.map((line) => [...line].length)) > 80);
});

test('splash uses the compact Rounded art on narrow terminals', () => {
  const logo = splashLogoForWidth(80);
  assert.equal(logo.length, 4);
  assert.match(logo.join('\n'), /[●◉]/);
});

test('splash uses the full Rounded art when it fits', () => {
  const logo = splashLogoForWidth(120);
  assert.equal(logo.length, 6);
  assert.ok(Math.max(...logo.map((line) => [...line].length)) > 80);
});
