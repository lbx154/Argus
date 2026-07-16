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

test('local endpoints get a default owner file and explicit configuration overrides it', () => {
  const savedOwner = process.env.ARGUS_TUI_API_OWNER_FILE;
  const savedHome = process.env.ARGUS_SKILL_HOME;
  try {
    delete process.env.ARGUS_TUI_API_OWNER_FILE;
    process.env.ARGUS_SKILL_HOME = '/state/argus';
    assert.equal(
      parseArgs(['--port', '8909']).ownerFile,
      '/state/argus/runtime/webapi-127.0.0.1-8909.owner.json',
    );
    assert.equal(parseArgs(['--host', '10.0.0.5']).ownerFile, undefined);

    process.env.ARGUS_TUI_API_OWNER_FILE = '/run/argus/owner.json';
    assert.equal(parseArgs([]).ownerFile, '/run/argus/owner.json');
  } finally {
    if (savedOwner === undefined) delete process.env.ARGUS_TUI_API_OWNER_FILE;
    else process.env.ARGUS_TUI_API_OWNER_FILE = savedOwner;
    if (savedHome === undefined) delete process.env.ARGUS_SKILL_HOME;
    else process.env.ARGUS_SKILL_HOME = savedHome;
  }
});

test('value flags reject missing and invalid values early', () => {
  assert.throws(() => parseArgs(['--host']), /--host requires a value/);
  assert.throws(() => parseArgs(['--port', '0']), /between 1 and 65535/);
  assert.throws(() => parseArgs(['--count', '1.5']), /positive integer/);
});
