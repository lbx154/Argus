import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { runFixture, overlay, manifest, main } from '../scripts/windows-install-preservation.mjs';

// A real private HTTP server tests the harness, not Argus product behavior.
const fakeCore = version => `import http from 'node:http';\nimport assert from 'node:assert/strict';\nassert.ok(!process.env.ARGUS_LETTERS); assert.ok(!process.env.OPENAI_API_KEY); assert.ok(!process.env.NODE_OPTIONS);\nconst server=http.createServer((req,res)=>{res.setHeader('content-type','application/json'); res.end(JSON.stringify({authentication:{authenticated:req.headers.authorization==='Bearer '+process.env.ARGUS_WEB_TOKEN},runtime:{pid:process.pid,state_root:process.env.ARGUS_HOME,package_version:${JSON.stringify(version)}}}));});\nserver.listen(0,'127.0.0.1',()=>console.log('Argus is at http://127.0.0.1:'+server.address().port+'/?token='+process.env.ARGUS_WEB_TOKEN));\nprocess.on('SIGTERM',()=>server.close());\n`;
function fixture(t) {
  // macOS exposes its system temp via /var -> /private/var. Canonicalize only
  // the test-owned root; runFixture must still reject all supplied link ancestors.
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'argus-preservation-')));
  t.after(() => fs.rmSync(root, { force: true, recursive: true }));
  for (const [dir, version] of [['old', 'old-fixture'], ['new', 'new-fixture']]) {
    fs.mkdirSync(path.join(root, dir, 'argus-core/dist'), { recursive: true });
    fs.writeFileSync(path.join(root, dir, 'Argus.exe'), 'MZ fixture ' + version);
    fs.writeFileSync(path.join(root, dir, 'WebView2Loader.dll'), 'fixture DLL ' + version);
    fs.writeFileSync(path.join(root, dir, 'argus-core/dist/argus.mjs'), fakeCore(version));
  }
  fs.writeFileSync(path.join(root, 'old/argus-core/old-only.txt'), 'old file retained on upgrade');
  fs.writeFileSync(path.join(root, 'new/argus-core/new-only.txt'), 'new file removed on rollback');
  return { root, oldDir: path.join(root, 'old'), newDir: path.join(root, 'new'), scratch: path.join(root, 'scratch 中文') };
}

test('overlay, three starts, rollback retain bytes AND original seed objects', async t => {
  const f = fixture(t), result = await runFixture(f);
  assert.equal(result.status, 'pass');
  assert.deepEqual(result.stages.map(row => row.core?.package_version).filter(Boolean), ['old-fixture', 'new-fixture', 'old-fixture']);
  assert.equal(result.stages.length, 6);
  assert.deepEqual(result.seed_after, result.seed_before);
  assert.equal(result.seed_integrity_pass, true);
  assert.ok(result.seed_before['shared/skills/user-owned/SKILL.md'], 'Use the actual ARGUS_SHARED skill location');
  assert.equal(fs.existsSync(path.join(result.install, 'argus-core/new-only.txt')), false);
  assert.equal(fs.existsSync(path.join(result.install, 'argus-core/old-only.txt')), true);
  assert.ok(!fs.readFileSync(path.join(f.scratch, 'evidence/upgraded-core.log'), 'utf8').includes('?token=fixture-only-'));
  await assert.rejects(runFixture(f), /Scratch must be NEW/);
});

test('long target: Windows installer guard fails closed; POSIX fixture can start', async t => {
  const f = { ...fixture(t), longPath: true };
  if (process.platform === 'win32') {
    await assert.rejects(runFixture(f), /Windows installer preflight blocked replacement \(exit 3\)/);
    const result = JSON.parse(fs.readFileSync(path.join(f.scratch, 'evidence/result.json'), 'utf8'));
    assert.ok(result.install_path_characters > 260);
    assert.equal(result.status, 'fail');
    assert.equal(result.seed_integrity_pass, true);
    assert.deepEqual(result.seed_after, result.seed_before);
    assert.equal(result.stages.length, 0, 'No payload replacement before safe preflight');
    assert.equal(fs.existsSync(path.join(result.install, 'Argus.exe')), false);
    assert.equal(fs.readFileSync(path.join(result.install, 'user-keep.txt'), 'utf8'), 'User file in installation; never remove.\n');
  } else {
    const result = await runFixture(f);
    assert.ok(result.install_path_characters > 260);
    assert.equal(result.status, 'pass');
  }
});

test('overlapping input/scratch and incomplete payload fail before scratch creation', async t => {
  const f = fixture(t);
  await assert.rejects(runFixture({ ...f, scratch: path.join(f.oldDir, 'nested') }), /overlap/);
  fs.unlinkSync(path.join(f.newDir, 'WebView2Loader.dll'));
  await assert.rejects(runFixture(f), /Missing payload/);
  assert.equal(fs.existsSync(f.scratch), false);
});

test('junction input and destination cannot redirect writes outside the install', async t => {
  const f = fixture(t), outside = path.join(f.root, 'outside'); fs.mkdirSync(outside);
  const marker = path.join(outside, 'marker'); fs.writeFileSync(marker, 'outside original');
  const link = path.join(f.root, 'linked'); fs.symlinkSync(outside, link, process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(runFixture({ ...f, scratch: path.join(link, 'scratch') }), /Links or junctions/);
  const install = path.join(f.root, 'install'); fs.mkdirSync(install);
  fs.symlinkSync(outside, path.join(install, 'argus-core'), process.platform === 'win32' ? 'junction' : 'dir');
  assert.throws(() => overlay(f.newDir, install, manifest(f.newDir)), /Links or junctions/);
  assert.equal(fs.readFileSync(marker, 'utf8'), 'outside original');
  assert.equal(fs.readdirSync(outside).length, 1);
  assert.equal(fs.existsSync(path.join(install, 'Argus.exe')), false, 'validate the entire plan before the first write');
});

test('Cargo-style hardlinked input is copied, but hardlinked destinations are refused', async t => {
  const f = fixture(t), inputAlias = path.join(f.root, 'build-output.exe');
  fs.linkSync(path.join(f.newDir, 'Argus.exe'), inputAlias);
  const result = await runFixture(f);
  assert.equal(result.status, 'pass');
  assert.equal(fs.statSync(path.join(f.scratch, 'new-stage/Argus.exe')).nlink, 1, 'Staging breaks source hardlink aliasing');
  assert.equal(fs.readFileSync(inputAlias, 'utf8'), 'MZ fixture new-fixture');
  const external = path.join(f.root, 'external.exe');
  const install = path.join(f.root, 'install'); fs.mkdirSync(install);
  fs.writeFileSync(external, 'external'); fs.linkSync(external, path.join(install, 'Argus.exe'));
  assert.throws(() => overlay(f.newDir, install, manifest(f.newDir)), /private regular file/);
  assert.equal(fs.readFileSync(external, 'utf8'), 'external');
});

test('altered source is rejected before any destination change', t => {
  const f = fixture(t), before = manifest(f.newDir), dest = path.join(f.root, 'dest'); fs.mkdirSync(dest);
  fs.appendFileSync(path.join(f.newDir, 'Argus.exe'), 'changed');
  assert.throws(() => overlay(f.newDir, dest, before), /changed/);
  assert.equal(fs.readdirSync(dest).length, 0);
});

test('a startup that rewrites seed bytes identically is not reported as retention', async t => {
  const f = fixture(t);
  const filename = path.join(f.newDir, 'argus-core/dist/argus.mjs');
  fs.writeFileSync(filename, `import fs from 'node:fs'; import path from 'node:path'; const file=path.join(process.env.ARGUS_HOME,'credentials.json'); fs.writeFileSync(file,fs.readFileSync(file));\n` + fakeCore('rewriter'));
  await assert.rejects(runFixture(f), /Seed replaced or changed/);
  const report = JSON.parse(fs.readFileSync(path.join(f.scratch, 'evidence/result.json'), 'utf8'));
  assert.equal(report.status, 'fail');
  assert.ok(report.stages.some(row => row.phase === 'upgraded-overlay'));
});

test('a startup that deletes and rebuilds an identical seed is detected', async t => {
  const f = fixture(t), filename = path.join(f.newDir, 'argus-core/dist/argus.mjs');
  fs.writeFileSync(filename, `import fs from 'node:fs'; import path from 'node:path'; const file=path.join(process.env.ARGUS_HOME,'credentials.json'); const bytes=fs.readFileSync(file); fs.unlinkSync(file); fs.writeFileSync(file,bytes);\n` + fakeCore('recreator'));
  await assert.rejects(runFixture(f), /Seed replaced or changed/);
});

test('traversing names and file-as-parent fail before any overlay write', t => {
  const f = fixture(t), dest = path.join(f.root, 'dest'); fs.mkdirSync(dest);
  assert.throws(() => overlay(f.newDir, dest, { '../escape': { bytes: 0, sha256: '' } }), /escapes destination/);
  fs.writeFileSync(path.join(dest, 'argus-core'), 'not a directory');
  assert.throws(() => overlay(f.newDir, dest, manifest(f.newDir)), /not a directory/);
  assert.equal(fs.existsSync(path.join(dest, 'Argus.exe')), false);
});

test('bad CLI options never create directories', async () => {
  await assert.rejects(main(['--scratch']), /Usage/);
  await assert.rejects(main(['--scratch', 'x', '--scratch', 'y']), /Duplicate/);
});

test('Windows running EXE is locked and existing installer guard blocks only the target', { skip: process.platform !== 'win32' }, async t => {
  const f = fixture(t), install = path.join(f.root, 'live 中文'), exe = path.join(install, 'Argus.exe'); fs.mkdirSync(install);
  fs.copyFileSync(process.execPath, exe);
  const child = spawn(exe, ['-e', 'console.log("fixture-ready");setInterval(()=>{},100000)'], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const closed = new Promise(resolve => child.once('close', resolve));
  try {
    await new Promise((resolve, reject) => { child.once('error', reject); child.stdout.once('data', resolve); });
    const guard = fileURLToPath(new URL('../scripts/installer-preflight.ps1', import.meta.url));
    const ps = path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
    const check = directory => spawnSync(ps, ['-NoProfile', '-NonInteractive', '-File', guard, '-InstallDirectory', directory, '-WaitSeconds', '0'], { windowsHide: true, encoding: 'utf8' });
    const blocked = check(install);
    assert.equal(blocked.status, 2, blocked.stdout + blocked.stderr);
    assert.throws(() => fs.copyFileSync(path.join(f.newDir, 'Argus.exe'), exe), error => ['EBUSY', 'EPERM', 'EACCES'].includes(error.code));
    assert.equal(child.exitCode, null, 'guard must not kill the running EXE');
    const other = path.join(f.root, 'other'); fs.mkdirSync(other);
    const unblocked = check(other);
    assert.equal(unblocked.status, 0, unblocked.stdout + unblocked.stderr);
  } finally { child.kill('SIGTERM'); await closed; }
});
