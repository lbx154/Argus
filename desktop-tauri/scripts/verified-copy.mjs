import fs from 'node:fs';
import path from 'node:path';

const CHUNK = 1024 * 1024;

/** Whether two regular files hold exactly the same bytes, compared directly. */
export function sameBytes(left, right) {
  if (fs.statSync(left).size !== fs.statSync(right).size) return false;
  const a = fs.openSync(left, 'r'), b = fs.openSync(right, 'r');
  const one = Buffer.allocUnsafe(CHUNK), two = Buffer.allocUnsafe(CHUNK);
  try {
    let count;
    while ((count = fs.readSync(a, one, 0, CHUNK, null)) > 0) {
      if (fs.readSync(b, two, 0, count, null) !== count || !one.subarray(0, count).equals(two.subarray(0, count))) return false;
    }
    return fs.readSync(b, two, 0, 1, null) === 0;
  } finally { fs.closeSync(a); fs.closeSync(b); }
}

/** Copy reviewed build inputs into a NEW stage, syncing every file and
 * comparing the staged bytes with their input.
 * Never use this helper on account homes; callers pass only build artifacts.
 * No existing destination or symlink is followed or silently replaced.
 */
export function copyVerified(source, destination) {
  const stat = fs.lstatSync(source);
  if (stat.isSymbolicLink()) throw new Error('Verified staging refuses linked build inputs.');
  if (fs.existsSync(destination)) throw new Error('Verified staging destination already exists.');
  if (stat.isDirectory()) {
    fs.mkdirSync(destination);
    let files = 0, bytes = 0;
    for (const name of fs.readdirSync(source).sort()) {
      const result = copyVerified(path.join(source, name), path.join(destination, name));
      files += result.files; bytes += result.bytes;
    }
    return { files, bytes };
  }
  if (!stat.isFile()) throw new Error('Verified staging accepts only regular files and directories.');
  const input = fs.openSync(source, 'r');
  let output;
  let bytes = 0;
  try {
    const before = fs.fstatSync(input, { bigint: true });
    output = fs.openSync(destination, 'wx');
    const buffer = Buffer.allocUnsafe(CHUNK);
    let count;
    while ((count = fs.readSync(input, buffer, 0, buffer.length, null)) > 0) {
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
  if (!sameBytes(source, destination)) throw new Error('Staged bytes do not match their build input.');
  fs.chmodSync(destination, stat.mode & 0o777);
  return { files: 1, bytes };
}
