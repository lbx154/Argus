import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { copyVerified } from './verified-copy.mjs';

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

function inventory(root, { placeholder = false } = {}) {
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
      const input = fs.openSync(filename, 'r');
      const hash = createHash('sha256');
      let bytes = 0;
      try {
        // Windows path-stat and handle-stat APIs can expose different file-ID
        // representations. Compare each API with itself across the read, not
        // an lstat inode with an fstat inode; still reject replacements/links.
        const before = fs.fstatSync(input, { bigint: true });
        if (!before.isFile() || before.size !== stat.size) throw new Error('Backend input changed before verification.');
        const buffer = Buffer.allocUnsafe(1024 * 1024);
        let count;
        while ((count = fs.readSync(input, buffer, 0, buffer.length, null)) > 0) {
          bytes += count; hash.update(buffer.subarray(0, count));
        }
        const after = fs.fstatSync(input, { bigint: true });
        const currentPath = fs.lstatSync(filename, { bigint: true });
        const same = (a, b) => ['dev', 'ino', 'size', 'mtimeNs', 'ctimeNs'].every(name => a[name] === b[name]);
        if (!same(before, after) || BigInt(bytes) !== before.size) {
          throw new Error('Backend input changed during verification (handle metadata).');
        }
        if (!currentPath.isFile() || currentPath.isSymbolicLink() || !same(stat, currentPath)) {
          throw new Error('Backend input changed during verification (path metadata).');
        }
      } finally { fs.closeSync(input); }
      files.push([entry, bytes, hash.digest('hex')]);
    }
  }
  noLinks(root);
  visit(root);
  return files;
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
  if (fs.existsSync(destination)) {
    const current = inventory(destination, { placeholder: true });
    if (JSON.stringify(current) === encoded) return { reused: true, entries: expected.length };
    if (current.length) throw new Error('Prepared backend differs; use a fresh output directory. Existing files were preserved.');
  } else {
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    noLinks(destination);
    fs.mkdirSync(destination);
  }
  for (const name of fs.readdirSync(source).sort()) copyVerified(path.join(source, name), path.join(destination, name));
  if (JSON.stringify(inventory(source)) !== encoded
      || JSON.stringify(inventory(destination, { placeholder: true })) !== encoded) {
    throw new Error('Backend payload changed or its staged bytes do not match.');
  }
  return { reused: false, entries: expected.length, sha256: createHash('sha256').update(encoded).digest('hex') };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 0 && args.length !== 2) throw new Error('Usage: stage-backend.mjs [SOURCE NEW_DESTINATION]');
    console.log(JSON.stringify(stageBackend(args.length ? { source: args[0], destination: args[1] } : {})));
  } catch (error) { console.error(`Backend was not staged: ${error.message}`); process.exitCode = 1; }
}
