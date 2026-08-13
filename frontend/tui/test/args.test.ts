import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import { parseArgs, withSelectedPort } from '../src/args.js';

test('legacy resume flags map onto the Ink project selection model', () => {
  assert.equal(parseArgs(['--resume', 's-paper']).project, 's-paper');
  assert.equal(parseArgs(['--resume']).resume, true);
  assert.equal(parseArgs(['resume']).resume, true);
  assert.equal(parseArgs(['--continue']).resume, true);
  assert.equal(parseArgs(['resume', '--all']).resumeAll, true);
  const fresh = parseArgs(['--resume', 's-old', '--new']);
  assert.equal(fresh.project, undefined);
  assert.equal(fresh.resume, false);
  assert.equal(fresh.forceNew, true);
});

test('exit policy defaults to detach and validates CLI/env values', () => {
  const saved = process.env.ARGUS_TUI_EXIT_POLICY;
  try {
    delete process.env.ARGUS_TUI_EXIT_POLICY;
    assert.equal(parseArgs([]).exitPolicy, 'detach');
    assert.equal(parseArgs(['--exit-policy', 'stop-api']).exitPolicy, 'stop-api');
    assert.equal(parseArgs(['--exit-policy', 'stop-all']).exitPolicy, 'stop-all');
    assert.throws(
      () => parseArgs(['--exit-policy', 'kill']),
      /must be detach, stop-api, or stop-all/,
    );
    process.env.ARGUS_TUI_EXIT_POLICY = 'stop-all';
    assert.equal(parseArgs([]).exitPolicy, 'stop-all');
  } finally {
    if (saved === undefined) delete process.env.ARGUS_TUI_EXIT_POLICY;
    else process.env.ARGUS_TUI_EXIT_POLICY = saved;
  }
});

test('local endpoints get a default owner file and explicit configuration overrides it', () => {
  const savedOwner = process.env.ARGUS_TUI_API_OWNER_FILE;
  const savedHome = process.env.ARGUS_SKILL_HOME;
  try {
    delete process.env.ARGUS_TUI_API_OWNER_FILE;
    process.env.ARGUS_SKILL_HOME = '/state/argus';
    assert.equal(
      parseArgs(['--port', '8909']).ownerFile,
      join(resolve('/state/argus'), 'runtime', 'webapi-127.0.0.1-8909.owner.json'),
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

test('Windows interactive launches open Web by default and allow opting out', () => {
  const windows = parseArgs([], { env: {}, platform: 'win32' });
  assert.equal(windows.openWebWithCli, true);
  assert.equal(windows.noOpen, false);
  assert.equal(parseArgs(['--no-open'], { env: {}, platform: 'win32' }).noOpen, true);
  assert.equal(parseArgs([], { env: {}, platform: 'linux' }).openWebWithCli, false);
});

test('ports are auto-selected unless pinned by CLI or environment', () => {
  const automatic = parseArgs([], { env: {}, platform: 'linux' });
  assert.equal(automatic.port, 8799);
  assert.equal(automatic.portExplicit, false);
  assert.equal(parseArgs(['--port', '8800'], { env: {} }).portExplicit, true);
  assert.equal(parseArgs([], { env: { ARGUS_TUI_PORT: '8801' } }).portExplicit, true);

  const moved = withSelectedPort(automatic, 8802);
  assert.equal(moved.port, 8802);
  assert.match(moved.ownerFile ?? '', /webapi-127\.0\.0\.1-8802\.owner\.json$/);
});

test('invalid CLI arguments print one actionable line without a bundle stack', () => {
  const cli = fileURLToPath(new URL('../src/cli.tsx', import.meta.url));
  const result = spawnSync(
    process.execPath,
    ['--import', 'tsx', cli, '--port', 'nope'],
    { encoding: 'utf8' },
  );

  assert.equal(result.status, 1);
  assert.equal(
    result.stderr,
    'argus: --port must be between 1 and 65535; got NaN\n',
  );
  assert.doesNotMatch(result.stderr, /\n\s+at /);
});
