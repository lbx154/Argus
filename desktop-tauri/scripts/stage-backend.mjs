import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { copyVerified, sameBytes } from './verified-copy.mjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function noLinks(filename) {
  let current = path.resolve(filename);
  while (true) {
    try { if (fs.lstatSync(current).isSymbolicLink()) throw new Error('Backend staging refuses links or junctions.'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    const parent = path.dirname(current);
    if (parent === current) return;
    current = parent;
  }
}

function inventory(root, { placeholder = false, stamps = false } = {}) {
  const files = [];
  function visit(directory, relative = '') {
    for (const name of fs.readdirSync(directory).sort()) {
      if (placeholder && !relative && name === '.gitkeep') continue;
      const filename = path.join(directory, name);
      const entry = path.posix.join(relative, name);
      const stat = fs.lstatSync(filename, { bigint: true });
      if (stat.isSymbolicLink()) throw new Error('Backend staging refuses linked payloads.');
      if (stat.isDirectory()) { files.push([entry + '/', 'directory']); visit(filename, entry); continue; }
      if (!stat.isFile()) throw new Error('Backend payload must contain only regular files.');
      // Stamps show a source file rewritten mid-stage; a copy has its own.
      files.push(stamps ? [entry, String(stat.size), String(stat.mtimeNs), String(stat.ctimeNs)] : [entry, String(stat.size)]);
    }
  }
  noLinks(root);
  visit(root);
  return files;
}

function sameFiles(source, destination, entries) {
  return entries.every(([entry, size]) => size === 'directory'
    || sameBytes(path.join(source, entry), path.join(destination, entry)));
}

/** Never erase a previous payload. A byte-identical prepared backend can be
 * reused; otherwise callers must build/stage in a fresh workspace or directory.
 */
export function stageBackend({ source = path.join(desktop, 'build/argus-backend'),
  destination = path.join(desktop, 'resources/argus-backend') } = {}) {
  source = path.resolve(source); destination = path.resolve(destination);
  if (source === destination || source.startsWith(destination + path.sep) || destination.startsWith(source + path.sep)) {
    throw new Error('Backend input and output must be separate directories.');
  }
  noLinks(source); noLinks(destination);
  if (!fs.statSync(path.join(source, 'argus-backend.exe')).isFile()) throw new Error('Frozen Windows backend is missing.');
  const expected = inventory(source);
  const encoded = JSON.stringify(expected);
  const stamped = JSON.stringify(inventory(source, { stamps: true }));
  if (fs.existsSync(destination)) {
    const current = inventory(destination, { placeholder: true });
    if (JSON.stringify(current) === encoded && sameFiles(source, destination, expected)) {
      return { reused: true, entries: expected.length };
    }
    if (current.length) throw new Error('Prepared backend differs; use a fresh output directory. Existing files were preserved.');
  } else {
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    noLinks(destination);
    fs.mkdirSync(destination);
  }
  for (const name of fs.readdirSync(source).sort()) copyVerified(path.join(source, name), path.join(destination, name));
  if (JSON.stringify(inventory(source, { stamps: true })) !== stamped
      || JSON.stringify(inventory(destination, { placeholder: true })) !== encoded) {
    throw new Error('Backend payload changed or its staged bytes do not match.');
  }
  return { reused: false, entries: expected.length };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 0 && args.length !== 2) throw new Error('Usage: stage-backend.mjs [SOURCE NEW_DESTINATION]');
    console.log(JSON.stringify(stageBackend(args.length ? { source: args[0], destination: args[1] } : {})));
  } catch (error) { console.error(`Backend was not staged: ${error.message}`); process.exitCode = 1; }
}
