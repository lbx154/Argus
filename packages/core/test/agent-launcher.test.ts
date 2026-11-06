import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import test from 'node:test';
import { launcher } from '../src/agent/sandbox.js';
import { mergeEnv } from '../src/commands.js';
import { Cli } from '../src/agent/session.js';

test('environment merge preserves literal special names and removes explicitly unset values', () => {
  const values = JSON.parse('{"__proto__":"literal registration","constructor":"literal constructor"}') as Record<string, string>;
  assert.deepEqual(mergeEnv({ ERASE: 'old', KEEP: 'first' }, values, { ERASE: undefined, KEEP: 'last' }), { ...values, KEEP: 'last' });
  assert.equal(Object.getPrototypeOf(mergeEnv(values)), Object.prototype);
});

// Native reproductions of explicit paths and npm's CRLF command shim, not a cmd.exe emulation.
const windows = process.platform === 'win32';
test('Windows launch resolves explicit .exe/.com paths, slash paths, extensions and relative paths', { skip: !windows }, t => {
  const root = mkdtempSync(join(tmpdir(), 'argus launcher 中文 '));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const ext of ['exe', 'com']) {
    const file = join(root, `native.${ext}`);
    copyFileSync(process.execPath, file);
    for (const path of [file, file.replaceAll('\\', '/'), relative(process.cwd(), file)]) {
      const argv = launcher(path);
      assert.equal(execFileSync(argv[0]!, [...argv.slice(1), '-p', '"原样 & % ! ^ ( )"'], { encoding: 'utf8' }).trim(), '原样 & % ! ^ ( )');
    }
  }
});

test('Windows PATH lookup honours explicit extensions, quoted entries and PATHEXT', { skip: !windows }, t => {
  const root = mkdtempSync(join(tmpdir(), 'argus path 中文 '));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const saved = { PATH: process.env.PATH, PATHEXT: process.env.PATHEXT };
  t.after(() => { for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
  copyFileSync(process.execPath, join(root, 'chosen.com'));
  copyFileSync(process.execPath, join(root, 'chosen.exe'));
  process.env.PATH = `"${root}"`;
  process.env.PATHEXT = '.COM;.EXE;.BAT;.CMD';
  assert.equal(launcher('chosen')[0], join(root, 'chosen.COM'));
  assert.equal(launcher('chosen.exe')[0], join(root, 'chosen.exe'));
});

test('Windows npm cmd/bat CRLF shims pass every argument literally to Node', { skip: !windows }, t => {
  const root = mkdtempSync(join(tmpdir(), 'argus shim 中文 '));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'scripts'));
  const script = join(root, 'scripts', 'echo.mjs');
  writeFileSync(script, 'console.log(JSON.stringify(process.argv.slice(2)))\n');
  const args = ['has spaces', '中文', '&echo NOT_SHELL', '%PATH%', '!x!', '^', 'a"b', '', 'trailing\\'];
  for (const ext of ['cmd', 'bat']) {
    const shim = join(root, `echo.${ext}`);
    writeFileSync(shim, '@ECHO off\r\nSET dp0=%~dp0\r\nSET "_prog=node"\r\n"%_prog%" "%dp0%\\scripts\\echo.mjs" %*\r\n');
    const argv = launcher(shim);
    assert.deepEqual(argv, [process.execPath, script]);
    assert.deepEqual(launcher(relative(process.cwd(), shim).replaceAll('\\', '/')), [process.execPath, script]);
    assert.deepEqual(JSON.parse(execFileSync(argv[0]!, [...argv.slice(1), ...args], { encoding: 'utf8' })), args);
  }
  const direct = join(root, 'direct.cmd');
  writeFileSync(direct, '@echo off\r\n"%~dp0\\node.exe" "%~dp0\\scripts\\echo.mjs" %*\r\n');
  assert.deepEqual(launcher(direct), [process.execPath, script]);
  // npm can also wrap a native executable; it must not be fed to Node.
  const exe = join(root, 'native.exe');
  copyFileSync(process.execPath, exe);
  writeFileSync(direct, '@echo off\r\n"%~dp0\\native.exe" %*\r\n');
  assert.deepEqual(launcher(direct), [exe]);
});

test('Windows lookup uses the effective child Path, PATHEXT and cwd; direct Node scripts need no shell', { skip: !windows }, t => {
  const root = mkdtempSync(join(tmpdir(), 'argus child env 中文 '));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const script = join(root, 'plain.mjs');
  writeFileSync(script, '#!/usr/bin/env node\r\nconsole.log("node script")\r\n');
  const shim = join(root, 'picked.cmd');
  writeFileSync(shim, '@echo off\r\n"%~dp0\\plain.mjs" %*\r\n');
  const env = { Path: `"${root}"`, PATHEXT: '.CMD;.EXE' };
  assert.deepEqual(launcher('picked', env, root), [process.execPath, script]);
  assert.deepEqual(launcher('./picked.cmd', env, root), [process.execPath, script]);
  assert.deepEqual(launcher(script, env, root), [process.execPath, script]);
  assert.equal(execFileSync(launcher(script, env, root)[0]!, [script], { encoding: 'utf8' }).trim(), 'node script');
});

test('the installed Windows Node wrapper accepts dp0 without an extra separator and a fixed argument prefix', { skip: !windows }, t => {
  const root = mkdtempSync(join(tmpdir(), 'argus wrapper 中文 '));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const script = join(root, 'launch-ai.mjs');
  writeFileSync(script, 'console.log(JSON.stringify(process.argv.slice(2)))\n');
  const shim = join(root, 'copilot.cmd');
  // Trimmed from the installed launcher observed by the real HTTP Settings probe.
  writeFileSync(shim, `@echo off\r\n"${process.execPath}" "%~dp0launch-ai.mjs" copilot %*\r\n`);
  const argv = launcher(shim);
  assert.deepEqual(argv, [process.execPath, script, 'copilot']);
  assert.deepEqual(JSON.parse(execFileSync(argv[0]!, [...argv.slice(1), '&literal', '%PATH%'], { encoding: 'utf8' })), ['copilot', '&literal', '%PATH%']);
  writeFileSync(shim, `@echo off\r\n"${process.execPath}" "%~dp0launch-ai.mjs" %UNCONTROLLED% %*\r\n`);
  assert.throws(() => launcher(shim), /without a shell/);
});

test('Windows CLI close retains a native tree-teardown failure after the root exit event', { skip: !windows }, async t => {
  const saved = { SystemRoot: process.env.SystemRoot, WINDIR: process.env.WINDIR };
  t.after(() => { for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
  const cli = new Cli([process.execPath, '-e', 'process.stdin.resume(); console.log(JSON.stringify({ready:true}));'], process.cwd(), mergeEnv(process.env));
  t.after(() => cli.close());
  assert.equal(await cli.until(message => message.ready === true), true);
  delete process.env.SystemRoot;
  delete process.env.WINDIR;
  await cli.close();
  assert.match(cli.gone ?? '', /Run cancelled\..*Unable to terminate process tree: SystemRoot is not set/);
});

test('Windows does not guess the meaning of arbitrary batch scripts', { skip: !windows }, t => {
  const root = mkdtempSync(join(tmpdir(), 'argus batch '));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const file = join(root, 'custom.bat');
  writeFileSync(file, '@echo off\r\ncall "elsewhere.exe" %*\r\n');
  assert.throws(() => launcher(file), /without a shell.*npm|npm.*shim/i);
});
