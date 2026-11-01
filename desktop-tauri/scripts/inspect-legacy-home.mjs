// Read-only preparation for Python -> TS migration, NOT an importer.
// Only session.json is read. History is counted by filename/size, never opened.
// No Python imports (its path resolver may rename old project directories),
// CLI execution, credential/config reads, workdir traversal, or writes.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const MAX_METADATA = 256 * 1024;
const MAX_ENTRIES = 10_000;
const sensitive = /(?:^\.env(?:\.|$)|^credentials\.|private.*key|token|secret)/i;
const historyName = /^(?:events\.jsonl(?:\.\d+)?|transcript\.jsonl|backlog(?:\.archive)?\.jsonl)$/;
const digest = value => createHash('sha256').update(value).digest('hex');

function noLinkedParents(filename) {
  for (let current = path.resolve(filename); ; current = path.dirname(current)) {
    if (sensitive.test(path.basename(current))) throw new Error('Sensitive source paths are not accepted.');
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) throw new Error('Source paths must not traverse links or junctions.');
    if (path.dirname(current) === current) break;
  }
}

function entries(directory) {
  const result = [];
  const dir = fs.opendirSync(directory);
  try {
    for (let entry; (entry = dir.readSync()); ) {
      if (result.length >= MAX_ENTRIES) throw new Error('Inventory limit exceeded; source needs manual review.');
      result.push(entry);
    }
  } finally { dir.closeSync(); }
  return result.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
}

function sameFile(a, b) {
  return a.dev === b.dev && a.ino === b.ino && a.size === b.size
    && a.mtimeNs === b.mtimeNs && a.ctimeNs === b.ctimeNs;
}

function readMetadata(filename) {
  noLinkedParents(filename);
  const before = fs.lstatSync(filename, { bigint: true });
  if (!before.isFile() || before.nlink !== 1n || before.size > BigInt(MAX_METADATA)) {
    throw new Error('Metadata must be a bounded, unlinked regular file.');
  }
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const opened = fs.fstatSync(fd, { bigint: true });
    if (!opened.isFile() || opened.nlink !== 1n || opened.size > BigInt(MAX_METADATA)) {
      throw new Error('Metadata must be a bounded, unlinked regular file.');
    }
    const buffer = Buffer.alloc(MAX_METADATA + 1);
    const count = fs.readSync(fd, buffer, 0, buffer.length, 0);
    // Compare each identity API with itself; Windows lstat/fstat dev values
    // need not be equal. Recheck the pathname AND the opened descriptor.
    noLinkedParents(filename);
    if (BigInt(count) !== opened.size
        || !sameFile(opened, fs.fstatSync(fd, { bigint: true }))
        || !sameFile(before, fs.lstatSync(filename, { bigint: true }))) {
      throw new Error('Source changed during inspection.');
    }
    const bytes = buffer.subarray(0, count);
    const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Invalid metadata.');
    return { value, sha256: digest(bytes) };
  } finally { fs.closeSync(fd); }
}

function workdirReference(meta) {
  // Match Python's precedence, never adopt launch_cwd or invent a workspace.
  for (const key of ['workdir', 'cwd']) {
    if (meta[key] == null || meta[key] === '') continue;
    if (typeof meta[key] !== 'string') return 'invalid';
    const value = meta[key].trim();
    if (!value) continue;
    if (value.includes('\0') || !path.isAbsolute(value)) return 'invalid';
    return key;
  }
  return 'missing';
}

function inspectProject(directory, name) {
  const result = {
    // Neither directory names, user text, nor workspace paths enter the report.
    sourceId: digest(name),
    layout: name.startsWith('s-') ? 'session' : /^[0-9a-f]{12}$/.test(name) ? 'old-fingerprint' : 'other-legacy',
    metadata: 'missing', workdirReference: 'missing', historyFiles: 0, historyBytes: 0,
    resumeSupported: false, action: 'manual-review', issues: [],
  };
  noLinkedParents(directory);
  const rows = entries(directory);
  for (const entry of rows) {
    if (!historyName.test(entry.name)) continue;
    const stat = fs.lstatSync(path.join(directory, entry.name));
    if (!stat.isFile() || stat.nlink !== 1) { result.issues.push('unsafe-history-entry'); continue; }
    result.historyFiles++;
    result.historyBytes += stat.size;
  }
  if (rows.some(e => e.name === 'project.json')) {
    result.issues.push('ts-or-mixed-layout');
    return result;
  }
  if (rows.some(e => e.name === 'backlog.commit.json')) result.issues.push('legacy-backlog-recovery-required');
  if (rows.some(e => e.name === 'session.json')) {
    try {
      const { value, sha256 } = readMetadata(path.join(directory, 'session.json'));
      if (value.id !== name) throw new Error('Metadata identity mismatch.');
      for (const key of ['display_name', 'objective', 'launch_cwd']) {
        if (value[key] != null && typeof value[key] !== 'string') throw new Error('Invalid metadata field.');
      }
      for (const key of ['created', 'last_active']) {
        if (value[key] != null && (typeof value[key] !== 'number' || !Number.isFinite(value[key]) || value[key] < 0)) {
          throw new Error('Invalid metadata timestamp.');
        }
      }
      result.metadata = 'valid';
      result.metadataSha256 = sha256;
      result.workdirReference = workdirReference(value);
    } catch {
      // Do not echo parser/filesystem errors: those can include user contents.
      result.metadata = 'invalid-or-unsafe';
      result.issues.push('metadata-needs-review');
    }
  }
  if (result.workdirReference === 'missing' || result.workdirReference === 'invalid') {
    result.issues.push('workdir-needs-review');
  }
  if (result.metadata === 'valid' && result.issues.length === 0) result.action = 'history-export-candidate';
  return result;
}

/** Explicit root only. No automatic discovery of the operator's data.
 * Even a history-export candidate needs a stopped-source snapshot, safe archive
 * export and a confirmed destination before any future import may be attempted.
 */
export function inspectLegacyHome(source) {
  if (typeof source !== 'string' || !path.isAbsolute(source)) throw new Error('An explicit absolute source root is required.');
  const root = path.resolve(source);
  noLinkedParents(root);
  if (!fs.lstatSync(root).isDirectory()) throw new Error('Source must be a directory.');
  const projects = path.join(root, 'projects');
  noLinkedParents(projects);
  if (!fs.lstatSync(projects).isDirectory()) throw new Error('Source has no projects directory.');
  const report = {
    format: 'argus-legacy-inventory-v1', mode: 'read-only', coverage: 'projects-only',
    sourceRootId: digest(process.platform === 'win32' ? root.toLowerCase() : root),
    migrationReady: false, resumeSupported: false, requiresStoppedSource: true,
    credentialsRead: false, historyContentsRead: false, writes: 0,
    excludedEntries: 0, projects: [],
  };
  for (const entry of entries(projects)) {
    if (entry.name.startsWith('.') || sensitive.test(entry.name)) { report.excludedEntries++; continue; }
    const directory = path.join(projects, entry.name);
    const stat = fs.lstatSync(directory);
    if (stat.isSymbolicLink() || !stat.isDirectory()) {
      report.excludedEntries++;
      continue;
    }
    report.projects.push(inspectProject(directory, entry.name));
  }
  return report;
}

export function main(args = process.argv.slice(2)) {
  if (args.length !== 2 || args[0] !== '--source') throw new Error('Usage: inspect-legacy-home.mjs --source ABSOLUTE_ROOT');
  return inspectLegacyHome(args[1]);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { console.log(JSON.stringify(main(), null, 2)); }
  catch { console.error('Legacy inventory refused; check the explicit root, permissions, limits and link safety. No import was performed.'); process.exitCode = 1; }
}
