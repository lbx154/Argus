import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { inspectLegacyHome, main } from '../scripts/inspect-legacy-home.mjs';

// Synthetic roots only; never discover or inspect the operator's legacy home.
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'argus-legacy-inspection-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'projects'));
  return {
    root,
    project(name = 's-fixture', values = {}) {
      const directory = path.join(root, 'projects', name);
      fs.mkdirSync(directory);
      fs.writeFileSync(path.join(directory, 'session.json'), JSON.stringify({
        id: name, created: 1_700_000_000, last_active: 1_700_000_010,
        display_name: 'Synthetic title not for reports', objective: 'Synthetic task not for reports',
        workdir: path.join(root, 'workspace-not-created'), ...values,
      }));
      return directory;
    },
  };
}

function snapshot(directory) {
  const out = {};
  const walk = folder => {
    for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
      const filename = path.join(folder, entry.name);
      if (entry.isDirectory()) walk(filename);
      else out[path.relative(directory, filename)] = createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
    }
  };
  walk(directory);
  return out;
}

test('inspection is deterministic and leaves all synthetic source bytes untouched', t => {
  const f = fixture(t); const dir = f.project();
  fs.writeFileSync(path.join(dir, 'events.jsonl'), 'synthetic event history\n');
  const before = snapshot(f.root);
  const first = inspectLegacyHome(f.root);
  assert.deepEqual(inspectLegacyHome(f.root), first);
  assert.deepEqual(snapshot(f.root), before);
  assert.equal(first.writes, 0);
  assert.equal(first.migrationReady, false);
  assert.equal(first.resumeSupported, false);
  assert.equal(first.requiresStoppedSource, true);
  assert.equal(first.projects[0].action, 'history-export-candidate');
  assert.equal(fs.existsSync(path.join(f.root, 'workspace-not-created')), false);
});

test('only session metadata is opened; histories, config and role sessions stay unread', t => {
  const f = fixture(t); const dir = f.project();
  for (const name of ['events.jsonl', 'events.jsonl.1', 'events.jsonl.27', 'transcript.jsonl', 'backlog.jsonl', 'backlog.archive.jsonl']) {
    fs.writeFileSync(path.join(dir, name), 'not parsed\n');
  }
  fs.writeFileSync(path.join(dir, 'events.jsonl.bak'), 'not part of canonical history');
  fs.writeFileSync(path.join(f.root, 'config.json'), 'must not open global configuration');
  fs.mkdirSync(path.join(dir, 'role-sessions'));
  fs.writeFileSync(path.join(dir, 'role-sessions', 'manager.json'), 'must not resume this session');
  const original = fs.openSync;
  const opened = [];
  t.mock.method(fs, 'openSync', (filename, ...args) => { opened.push(filename); return original(filename, ...args); });
  const report = inspectLegacyHome(f.root);
  assert.deepEqual(opened, [path.join(dir, 'session.json')]);
  assert.equal(report.projects[0].historyFiles, 6);
  assert.equal(report.projects[0].historyBytes, 6 * Buffer.byteLength('not parsed\n'));
  const output = JSON.stringify(report);
  for (const text of ['Synthetic title', 'Synthetic task', 'workspace-not-created', 's-fixture', f.root]) {
    assert.equal(output.includes(text), false);
  }
});

test('workdir wins, cwd is a legacy fallback, launch_cwd is never adopted', t => {
  const f = fixture(t);
  f.project('s-a', { cwd: path.join(f.root, 'other') });
  f.project('s-b', { workdir: '', cwd: path.join(f.root, 'fallback') });
  f.project('s-c', { workdir: '', cwd: '', launch_cwd: path.join(f.root, 'launch') });
  f.project('s-d', { workdir: 'relative-path', cwd: path.join(f.root, 'must-not-fallback') });
  const rows = inspectLegacyHome(f.root).projects;
  assert.deepEqual(rows.map(row => row.workdirReference), ['workdir', 'cwd', 'missing', 'invalid']);
  assert.deepEqual(rows.map(row => row.action), ['history-export-candidate', 'history-export-candidate', 'manual-review', 'manual-review']);
});

test('old hash and encoded fingerprint layouts are not dropped or reinterpreted as TS', t => {
  const f = fixture(t);
  fs.mkdirSync(path.join(f.root, 'projects', '012345abcdef'));
  const directory = path.join(f.root, 'projects', 'D%3A%5Clegacy');
  fs.mkdirSync(directory); fs.writeFileSync(path.join(directory, 'events.jsonl'), 'history');
  const rows = inspectLegacyHome(f.root).projects;
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map(row => row.layout), ['old-fingerprint', 'other-legacy']);
  assert.ok(rows.every(row => row.metadata === 'missing' && row.action === 'manual-review' && !row.resumeSupported));
});

test('malformed, mismatched and oversized metadata fail closed without losing other projects', t => {
  const f = fixture(t);
  const a = f.project('s-a'); fs.writeFileSync(path.join(a, 'session.json'), '{invalid user content');
  f.project('s-b', { id: 's-other' });
  const c = f.project('s-c'); fs.writeFileSync(path.join(c, 'session.json'), ' '.repeat(256 * 1024 + 1));
  f.project('s-d');
  const report = inspectLegacyHome(f.root);
  assert.deepEqual(report.projects.map(row => row.metadata), ['invalid-or-unsafe', 'invalid-or-unsafe', 'invalid-or-unsafe', 'valid']);
  assert.equal(JSON.stringify(report).includes('invalid user content'), false);
});

test('invalid timestamps, metadata types and UTF-8 cannot become export candidates', t => {
  const f = fixture(t);
  f.project('s-a', { created: 'yesterday' });
  f.project('s-b', { display_name: { unexpected: true } });
  const c = f.project('s-c'); fs.writeFileSync(path.join(c, 'session.json'), Buffer.from([0xff]));
  assert.ok(inspectLegacyHome(f.root).projects.every(row => row.action === 'manual-review'));
});

test('TS and mixed layouts are not migrated twice, even if they contain session.json', t => {
  const f = fixture(t); const dir = f.project('p-1234');
  fs.writeFileSync(path.join(dir, 'project.json'), '{}');
  t.mock.method(fs, 'openSync', () => { throw new Error('No metadata should be opened for TS layouts'); });
  const row = inspectLegacyHome(f.root).projects[0];
  assert.ok(row.issues.includes('ts-or-mixed-layout'));
  assert.equal(row.action, 'manual-review');
});

test('pending legacy backlog commit is a recovery blocker, not inferred completed work', t => {
  const f = fixture(t); const dir = f.project();
  fs.writeFileSync(path.join(dir, 'backlog.commit.json'), 'must not parse/replay this');
  const row = inspectLegacyHome(f.root).projects[0];
  assert.equal(row.action, 'manual-review');
  assert.ok(row.issues.includes('legacy-backlog-recovery-required'));
});

test('linked source parents are refused; linked project directories are never followed', t => {
  const f = fixture(t); const target = f.project('s-target');
  const link = path.join(f.root, 'projects', 's-link');
  try { fs.symlinkSync(target, link, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Host cannot create test links'); throw error; }
  const report = inspectLegacyHome(f.root);
  assert.equal(report.projects.length, 1);
  assert.equal(report.excludedEntries, 1);
  assert.throws(() => inspectLegacyHome(link), /links or junctions/);
});

test('hardlinked metadata is not opened and cannot borrow data from another file', t => {
  const f = fixture(t); const dir = f.project();
  try { fs.linkSync(path.join(dir, 'session.json'), path.join(f.root, 'alias.json')); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) return t.skip('Host cannot create test hardlinks'); throw error; }
  let opened = 0;
  t.mock.method(fs, 'openSync', () => { opened++; throw new Error('Hardlinks must be refused before opening'); });
  assert.equal(inspectLegacyHome(f.root).projects[0].metadata, 'invalid-or-unsafe');
  assert.equal(opened, 0);
});

test('non-file history entries are flagged without opening or traversing them', t => {
  const f = fixture(t); const dir = f.project();
  fs.mkdirSync(path.join(dir, 'events.jsonl'));
  fs.writeFileSync(path.join(dir, 'events.jsonl', 'unrelated.json'), 'must not traverse');
  const row = inspectLegacyHome(f.root).projects[0];
  assert.equal(row.historyFiles, 0);
  assert.ok(row.issues.includes('unsafe-history-entry'));
  assert.equal(row.action, 'manual-review');
});

test('metadata changed during its read is not accepted as a stable inventory entry', t => {
  const f = fixture(t); f.project();
  const original = fs.fstatSync; let calls = 0;
  t.mock.method(fs, 'fstatSync', (...args) => {
    const stat = original(...args);
    if (++calls < 2) return stat;
    return new Proxy(stat, { get(target, name) { return name === 'size' ? target.size + 1n : Reflect.get(target, name); } });
  });
  assert.equal(inspectLegacyHome(f.root).projects[0].metadata, 'invalid-or-unsafe');
});

test('explicit root is required and unknown/duplicate command options cannot initiate a scan', () => {
  assert.throws(() => inspectLegacyHome(), /explicit absolute/);
  assert.throws(() => inspectLegacyHome('relative'), /explicit absolute/);
  assert.throws(() => main([]), /Usage/);
  assert.throws(() => main(['--source', 'x', '--source', 'y']), /Usage/);
  assert.throws(() => main(['--apply', 'x']), /Usage/);
});
