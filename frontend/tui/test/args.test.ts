import assert from 'node:assert/strict';
import test from 'node:test';

import { parseArgs } from '../src/args.js';

test('legacy resume flags map onto the Ink project selection model', () => {
  assert.equal(parseArgs(['--resume', 's-paper']).project, 's-paper');
  assert.equal(parseArgs(['--resume']).resume, true);
  assert.equal(parseArgs(['resume']).resume, true);
  assert.equal(parseArgs(['--continue']).resume, true);
  assert.equal(parseArgs(['resume', '--all']).resumeAll, true);
  const fresh = parseArgs(['--resume', 's-old', '--new']);
  assert.equal(fresh.project, undefined);
  assert.equal(fresh.resume, false);
});

test('value flags reject missing and invalid values early', () => {
  assert.throws(() => parseArgs(['--host']), /--host requires a value/);
  assert.throws(() => parseArgs(['--port', '0']), /between 1 and 65535/);
  assert.throws(() => parseArgs(['--count', '1.5']), /positive integer/);
});
