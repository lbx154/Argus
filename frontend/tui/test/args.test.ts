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

test('ownerFile is read from ARGUS_TUI_API_OWNER_FILE and forwarded to ensureApi', () => {
  const saved = process.env.ARGUS_TUI_API_OWNER_FILE;
  try {
    process.env.ARGUS_TUI_API_OWNER_FILE = '/run/argus/owner.json';
    assert.equal(parseArgs([]).ownerFile, '/run/argus/owner.json');
  } finally {
    if (saved === undefined) delete process.env.ARGUS_TUI_API_OWNER_FILE;
    else process.env.ARGUS_TUI_API_OWNER_FILE = saved;
  }
});

test('value flags reject missing and invalid values early', () => {
  assert.throws(() => parseArgs(['--host']), /--host requires a value/);
  assert.throws(() => parseArgs(['--port', '0']), /between 1 and 65535/);
  assert.throws(() => parseArgs(['--count', '1.5']), /positive integer/);
});
