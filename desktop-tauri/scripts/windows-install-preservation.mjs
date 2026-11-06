#!/usr/bin/env node
// Scratch-only overlay/rollback fixture; never installs NSIS or opens a desktop.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { copyVerified, sameBytes } from './verified-copy.mjs';

const roots = ['Argus.exe', 'WebView2Loader.dll', 'argus-core', 'licenses'];
const required = ['Argus.exe', 'WebView2Loader.dll', 'argus-core/dist/argus.mjs'];
const sha = filename => createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
const inside = (parent, child) => { const r = path.relative(parent, child); return r === '' || !r.startsWith('..' + path.sep) && r !== '..' && !path.isAbsolute(r); };
const save = (filename, value) => fs.writeFileSync(filename, JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });

export function noLinks(filename) {
  let current = path.resolve(filename);
  for (;;) {
    try { if (fs.lstatSync(current).isSymbolicLink()) throw new Error('Links or junctions are not allowed: ' + current); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
}

export function manifest(root, prefix = '') {
  noLinks(root);
  const result = {};
  function visit(relative) {
    const filename = path.join(root, relative), stat = fs.lstatSync(filename);
    assert.ok(!stat.isSymbolicLink(), 'Linked payload: ' + filename);
    if (stat.isDirectory()) {
      for (const name of fs.readdirSync(filename).sort()) visit(path.join(relative, name));
    } else {
      assert.ok(stat.isFile(), 'Only regular payload files are accepted: ' + filename);
      result[relative.split(path.sep).join('/')] = { bytes: stat.size, sha256: sha(filename) };
    }
  }
  visit(prefix);
  return result;
}

function payload(root) {
  const result = {};
  noLinks(root);
  for (const name of roots) {
    if (fs.existsSync(path.join(root, name))) Object.assign(result, manifest(root, name));
  }
  for (const name of required) assert.ok(result[name], 'Missing payload: ' + name);
  return result;
}

function verifyFiles(root, expected) {
  for (const [name, receipt] of Object.entries(expected)) {
    const filename = path.join(root, name);
    noLinks(filename);
    const stat = fs.lstatSync(filename);
    assert.ok(stat.isFile(), 'Not a regular file: ' + filename);
    assert.equal(stat.size, receipt.bytes, 'Size changed: ' + name);
    assert.equal(sha(filename), receipt.sha256, 'SHA changed: ' + name);
  }
}

// The caller owns a NEW scratch tree, never a user's installation. Existing
// unknown files are not removed. Plan validation precedes the first write.
export function overlay(source, destination, expected) {
  noLinks(source); noLinks(destination);
  for (const name of Object.keys(expected)) {
    assert.ok(name && !path.isAbsolute(name) && inside(source, path.resolve(source, name)) && inside(destination, path.resolve(destination, name)), 'Payload escapes destination');
    noLinks(path.join(destination, name));
    let parent = path.dirname(path.join(destination, name));
    while (inside(destination, parent)) {
      if (fs.existsSync(parent)) assert.ok(fs.statSync(parent).isDirectory(), 'Destination parent is not a directory');
      if (parent === destination) break;
      parent = path.dirname(parent);
    }
    if (fs.existsSync(path.join(destination, name))) {
      const s = fs.lstatSync(path.join(destination, name));
      assert.ok(s.isFile() && s.nlink === 1, 'Destination is not a private regular file');
    }
  }
  verifyFiles(source, expected);
  for (const name of Object.keys(expected)) {
    const from = path.join(source, name), to = path.join(destination, name);
    fs.mkdirSync(path.dirname(to), { recursive: true });
    fs.copyFileSync(from, to);
    const fd = fs.openSync(to, 'r+');
    try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    assert.ok(sameBytes(from, to), 'Overlay bytes differ: ' + name);
  }
  verifyFiles(destination, expected);
}

function identity(filename) {
  const s = fs.statSync(filename, { bigint: true });
  return { dev: String(s.dev), ino: String(s.ino), birth_ns: String(s.birthtimeNs), mtime_ns: String(s.mtimeNs), ctime_ns: String(s.ctimeNs), sha256: sha(filename), bytes: Number(s.size) };
}

function isolatedEnv(scratch, state, shared) {
  const env = {};
  // No inherited account, proxy, NODE_OPTIONS, agent override or Argus variables.
  for (const key of ['SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT']) if (process.env[key]) env[key] = process.env[key];
  const home = path.join(scratch, 'user');
  return { ...env, PATH: [path.dirname(process.execPath), ...(process.platform === 'win32' ? [path.join(env.SystemRoot || 'C:/Windows', 'System32')] : ['/usr/bin', '/bin'])].join(path.delimiter),
    HOME: home, USERPROFILE: home, APPDATA: path.join(home, 'AppData/Roaming'), LOCALAPPDATA: path.join(home, 'AppData/Local'),
    TEMP: path.join(scratch, 'temp'), TMP: path.join(scratch, 'temp'), TMPDIR: path.join(scratch, 'temp'),
    ARGUS_HOME: state, ARGUS_SHARED: shared, ARGUS_WEB_TOKEN: 'fixture-only-' + randomUUID(), ARGUS_DESKTOP_DISABLE_UPDATE_CHECK: '1' };
}

async function getMeta(url, token) {
  return new Promise((resolve, reject) => {
    const request = http.get(url + '/api/meta', { headers: { authorization: 'Bearer ' + token } }, response => {
      let body = ''; response.setEncoding('utf8'); response.on('data', data => { body += data; });
      response.on('end', () => { try { assert.equal(response.statusCode, 200); resolve(JSON.parse(body)); } catch (error) { reject(error); } });
    });
    request.on('error', reject);
    request.setTimeout(15000, () => request.destroy(new Error('Fixture HTTP request timed out')));
  });
}

// Real built core, one readiness event and one authenticated HTTP read. No agent
// call, account, model, UI polling, production single-instance activation or retry.
export async function probeCore(install, env, evidence, phase) {
  const child = spawn(process.execPath, [path.join(install, 'argus-core/dist/argus.mjs'), '--port', '0', '--backend', 'pi'],
    { cwd: env.ARGUS_HOME, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '', ended = false, rejectReady;
  const closed = new Promise(resolve => child.once('close', (code, signal) => { ended = true; resolve({ code, signal }); }));
  const ready = new Promise((resolve, reject) => {
    rejectReady = reject;
    child.once('error', reject);
    child.once('exit', code => reject(new Error('Core exited before readiness: ' + code)));
    child.stdout.on('data', bytes => {
      output += bytes.toString();
      const match = /Argus is at (http:\/\/127\.0\.0\.1:\d+)\//.exec(output);
      if (match) resolve(match[1]);
    });
  });
  child.stderr.on('data', bytes => { output += bytes.toString(); });
  const timer = setTimeout(() => rejectReady(new Error('Fixture core readiness timed out')), 30000);
  let meta;
  try {
    const url = await ready;
    meta = await getMeta(url, env.ARGUS_WEB_TOKEN);
    assert.equal(meta.authentication.authenticated, true);
    assert.equal(meta.runtime.pid, child.pid);
    assert.equal(path.resolve(meta.runtime.state_root), path.resolve(env.ARGUS_HOME));
  } finally {
    clearTimeout(timer);
    // This probe makes no agent/worker calls: terminate only its own child via
    // the retained ChildProcess handle, never by process name or recycled PID.
    if (!ended) child.kill('SIGTERM');
    const exit = await closed;
    fs.writeFileSync(path.join(evidence, phase + '-core.log'), output.replace(/([?&]token=)[^\s&]+/g, '$1[redacted]'), { flag: 'wx' });
    save(path.join(evidence, phase + '-exit.json'), exit);
  }
  return { pid: meta.runtime.pid, package_version: meta.runtime.package_version, authenticated: true, state_root: meta.runtime.state_root };
}

function preflight(install) {
  if (process.platform !== 'win32') return;
  const system = process.env.SystemRoot || process.env.WINDIR;
  assert.ok(system, 'SystemRoot is needed for Windows installer preflight');
  try {
    execFileSync(path.join(system, 'System32/WindowsPowerShell/v1.0/powershell.exe'),
      ['-NoLogo', '-NoProfile', '-NonInteractive', '-File', fileURLToPath(new URL('./installer-preflight.ps1', import.meta.url)), '-InstallDirectory', install, '-WaitSeconds', '0'],
      { windowsHide: true, stdio: 'pipe', encoding: 'utf8' });
  } catch (error) {
    throw new Error('Windows installer preflight blocked replacement (exit ' + error.status + '): ' + String(error.stdout || error.message).trim());
  }
}

export async function runFixture({ oldDir, newDir, scratch, longPath = false }) {
  oldDir = path.resolve(oldDir); newDir = path.resolve(newDir); scratch = path.resolve(scratch);
  for (const dir of [oldDir, newDir, scratch]) noLinks(dir);
  assert.ok(!fs.existsSync(scratch), 'Scratch must be NEW; no reuse, deletion or rebuilding');
  assert.ok(fs.statSync(path.dirname(scratch)).isDirectory(), 'Scratch parent must already exist');
  for (const dir of [oldDir, newDir]) assert.ok(!inside(dir, scratch) && !inside(scratch, dir), 'Scratch must not overlap payload inputs');
  const oldManifest = payload(oldDir), newManifest = payload(newDir);
  const install = path.join(scratch, '安装 空格 & 100%', ...(longPath ? Array(7).fill('长路径 segment 0123456789abcdef') : []), 'Argus');
  fs.mkdirSync(scratch);
  const evidence = path.join(scratch, 'evidence'); fs.mkdirSync(evidence);
  const state = path.join(scratch, 'state'), shared = path.join(scratch, 'shared');
  const env = isolatedEnv(scratch, state, shared);
  const seed = {
    'state/projects/p-preservation/project.json': JSON.stringify({ id: 'p-preservation', created: '2026-01-01T00:00:00Z', workdir: path.join(scratch, 'work'), request: 'Seed only; never run', backend: 'pi', model: 'fixture', effort: 'low', status: 'done', error: null, questions: [], tasks: [] }),
    'state/projects/p-preservation/events.jsonl': '{"ts":"2026-01-01T00:00:00Z","type":"fixture.seed"}\n',
    'state/credentials.json': '{"ARGUS_PRESERVATION_PLACEHOLDER":"NOT-A-REAL-CREDENTIAL-中文"}\n',
    'shared/skills/user-owned/SKILL.md': '---\nname: user-owned\ndescription: preservation fixture only\n---\n用户原始技能，不得重建。\n',
    'user/AppData/Roaming/argus-desktop/settings.json': '{"port":8799,"token":"NOT-A-REAL-TOKEN","runnerKind":"pi","setupComplete":false,"runnerConfigured":false,"runnerBins":{},"appearanceTheme":"dark"}\n',
    'outside-install-canary.txt': 'Not a payload file. Must stay identical.\n',
  };
  for (const dir of [state, shared, env.APPDATA, env.LOCALAPPDATA, env.HOME, env.TEMP, path.join(scratch, 'work')]) fs.mkdirSync(dir, { recursive: true });
  fs.mkdirSync(path.dirname(install), { recursive: true });
  fs.mkdirSync(install);
  fs.writeFileSync(path.join(install, 'user-keep.txt'), 'User file in installation; never remove.\n');
  for (const [name, text] of Object.entries(seed)) { const filename = path.join(scratch, name); fs.mkdirSync(path.dirname(filename), { recursive: true }); fs.writeFileSync(filename, text, { flag: 'wx' }); }
  const protectedPaths = [...Object.keys(seed).map(name => path.join(scratch, name)), path.join(install, 'user-keep.txt')];
  // Keep the original file objects open throughout: identical bytes after
  // deleting/recreating cannot pass as retention on Windows or POSIX.
  const handles = protectedPaths.map(filename => fs.openSync(filename, 'r'));
  const baseline = Object.fromEntries(protectedPaths.map(filename => [path.relative(scratch, filename).split(path.sep).join('/'), identity(filename)]));
  const installIdentity = fs.statSync(install, { bigint: true });
  const report = { kind: 'scratch-payload-overlay-not-NSIS-install', started_at: new Date().toISOString(), node: process.version, platform: process.platform, scratch, install, install_path_characters: install.length, old_source: oldDir, new_source: newDir, old_payload: oldManifest, new_payload: newManifest, seed_before: baseline, stages: [], status: 'running' };
  function retained(phase) {
    for (const [name, expected] of Object.entries(baseline)) assert.deepEqual(identity(path.join(scratch, name)), expected, 'Seed replaced or changed: ' + name);
    for (const fd of handles) assert.ok(fs.fstatSync(fd).nlink > 0, 'Original seed object was unlinked');
    const now = fs.statSync(install, { bigint: true });
    assert.equal(now.ino, installIdentity.ino); assert.equal(now.birthtimeNs, installIdentity.birthtimeNs);
    report.stages.push({ phase, seeds_identical: true, original_objects_retained: true });
  }
  try {
    for (const [source, dest] of [[oldDir, 'old-backup'], [newDir, 'new-stage']]) {
      fs.mkdirSync(path.join(scratch, dest));
      for (const name of roots) if (fs.existsSync(path.join(source, name))) copyVerified(path.join(source, name), path.join(scratch, dest, name));
    }
    save(path.join(evidence, 'seed-before.json'), baseline);
    for (const [phase, source, expected] of [['old', path.join(scratch, 'old-backup'), oldManifest], ['upgraded', path.join(scratch, 'new-stage'), newManifest], ['rollback', path.join(scratch, 'old-backup'), oldManifest]]) {
      preflight(install);
      overlay(source, install, expected);
      if (phase === 'rollback') {
        // Remove ONLY newly introduced, still-unchanged manifest-owned files.
        for (const name of Object.keys(newManifest)) if (!oldManifest[name]) {
          verifyFiles(install, { [name]: newManifest[name] });
          fs.unlinkSync(path.join(install, name));
        }
      }
      retained(phase + '-overlay');
      const startup = await probeCore(install, env, evidence, phase);
      retained(phase + '-startup');
      report.stages.at(-1).core = startup;
      verifyFiles(install, expected);
    }
    assert.deepEqual(payload(install), oldManifest, 'Rollback must restore the exact old owned payload');
    assert.deepEqual(payload(oldDir), oldManifest, 'Old input changed'); assert.deepEqual(payload(newDir), newManifest, 'New input changed');
    report.status = 'pass';
    return report;
  } catch (error) { report.status = 'fail'; report.error = error.message; throw error; }
  finally {
    try {
      report.seed_after = Object.fromEntries(protectedPaths.map(filename => [path.relative(scratch, filename).split(path.sep).join('/'), identity(filename)]));
      report.seed_integrity_pass = JSON.stringify(report.seed_after) === JSON.stringify(baseline) && handles.every(fd => fs.fstatSync(fd).nlink > 0);
    } catch (error) { report.seed_integrity_pass = false; report.seed_check_error = error.message; }
    for (const fd of handles) fs.closeSync(fd);
    const lostSeed = report.status === 'pass' && !report.seed_integrity_pass;
    if (lostSeed) { report.status = 'fail'; report.error = 'Seed integrity failed at final receipt'; }
    save(path.join(evidence, 'result.json'), report);
    if (lostSeed) throw new Error(report.error);
  }
}

export async function main(args) {
  if (args.length === 1 && args[0] === '--help') { console.log('Usage: node windows-install-preservation.mjs --old-dir COMPLETE_PAYLOAD --new-dir COMPLETE_PAYLOAD --scratch NEW_DIRECTORY [--long-path yes]\nScratch-only, no registry/NSIS/native UI. Starts old/new/rollback bundled core via this Node; all state is seeded and retained. Trusted build inputs only.'); return; }
  const options = {};
  for (let i = 0; i < args.length; i += 2) {
    assert.ok(['--old-dir', '--new-dir', '--scratch', '--long-path'].includes(args[i]) && args[i + 1], 'Usage: --old-dir DIR --new-dir DIR --scratch NEW_DIR [--long-path yes]');
    assert.ok(!Object.hasOwn(options, args[i]), 'Duplicate option'); options[args[i]] = args[i + 1];
  }
  assert.ok(options['--old-dir'] && options['--new-dir'] && options['--scratch'], 'Missing required directory');
  assert.ok(!options['--long-path'] || ['yes', 'no'].includes(options['--long-path']), 'Expected --long-path yes or no');
  const result = await runFixture({ oldDir: options['--old-dir'], newDir: options['--new-dir'], scratch: options['--scratch'], longPath: options['--long-path'] === 'yes' });
  console.log(JSON.stringify({ status: result.status, report: path.join(result.scratch, 'evidence/result.json'), install_path_characters: result.install_path_characters }));
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2)).catch(error => { console.error(error.message); process.exitCode = 1; });
