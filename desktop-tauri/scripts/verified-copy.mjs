import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

function fileDigest(filename) {
  const fd = fs.openSync(filename, 'r');
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  const digest = createHash('sha256');
  try {
    let count;
    while ((count = fs.readSync(fd, buffer, 0, buffer.length, null)) > 0) digest.update(buffer.subarray(0, count));
  } finally { fs.closeSync(fd); }
  return digest.digest('hex');
}

/** Copy reviewed build inputs into a NEW stage, syncing and verifying every file.
 * Never use this helper on account homes; callers pass only build artifacts.
 * No existing destination or symlink is followed or silently replaced.
 */
export function copyVerified(source, destination) {
  const stat = fs.lstatSync(source);
  if (stat.isSymbolicLink()) throw new Error('Verified staging refuses linked build inputs.');
  if (fs.existsSync(destination)) throw new Error('Verified staging destination already exists.');
  if (stat.isDirectory()) {
    fs.mkdirSync(destination);
    const tree = createHash('sha256');
    let files = 0, bytes = 0;
    for (const name of fs.readdirSync(source).sort()) {
      const result = copyVerified(path.join(source, name), path.join(destination, name));
      tree.update(name).update('\0').update(result.sha256).update('\0');
      files += result.files; bytes += result.bytes;
    }
    return { files, bytes, sha256: tree.digest('hex') };
  }
  if (!stat.isFile()) throw new Error('Verified staging accepts only regular files and directories.');
  const input = fs.openSync(source, 'r');
  let output;
  const digest = createHash('sha256');
  let bytes = 0;
  try {
    const before = fs.fstatSync(input, { bigint: true });
    output = fs.openSync(destination, 'wx');
    const buffer = Buffer.allocUnsafe(1024 * 1024);
    let count;
    while ((count = fs.readSync(input, buffer, 0, buffer.length, null)) > 0) {
      digest.update(buffer.subarray(0, count));
      let written = 0;
      while (written < count) {
        const amount = fs.writeSync(output, buffer, written, count - written);
        if (amount <= 0) throw new Error('Staging copy stopped making progress.');
        written += amount;
      }
      bytes += count;
    }
    fs.fsyncSync(output);
    const after = fs.fstatSync(input, { bigint: true });
    if (before.size !== after.size || before.mtimeNs !== after.mtimeNs || BigInt(bytes) !== before.size) {
      throw new Error('A build input changed during staging.');
    }
  } finally {
    fs.closeSync(input);
    if (output !== undefined) fs.closeSync(output);
  }
  const expected = digest.digest('hex');
  if (fileDigest(destination) !== expected) throw new Error('Staged bytes do not match their build input.');
  fs.chmodSync(destination, stat.mode & 0o777);
  return { files: 1, bytes, sha256: expected };
}
