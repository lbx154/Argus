import assert from 'node:assert/strict';
import test from 'node:test';
import { splashLogoForWidth } from '../src/components/Splash.js';

test('splash uses the compact banner on narrow terminals', () => {
  const logo = splashLogoForWidth(80);

  assert.equal(logo.length, 5);
  assert.match(logo.join('\n'), /___/);
});

test('splash restores the full ARGUS-SKILL banner when it fits', () => {
  const logo = splashLogoForWidth(120);

  assert.equal(logo.length, 6);
  assert.ok(Math.max(...logo.map((line) => [...line].length)) > 80);
});
