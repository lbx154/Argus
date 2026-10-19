import fs from 'node:fs';
import { createPublicKey, verify } from 'node:crypto';

function base64(text, label, length) {
  if (typeof text !== 'string' || !text.length || text.length > 8192
      || !/^[A-Za-z0-9+/]+={0,2}$/.test(text)) {
    throw new Error(`Invalid ${label} encoding.`);
  }
  const bytes = Buffer.from(text, 'base64');
  if (bytes.toString('base64') !== text || (length !== undefined && bytes.length !== length)) {
    throw new Error(`Invalid ${label} length or encoding.`);
  }
  return bytes;
}

function lines(encoded, label, count) {
  if (typeof encoded !== 'string') throw new Error(`Missing ${label}.`);
  const text = base64(encoded.trim(), label).toString('utf8');
  const result = text.trimEnd().split(/\r?\n/);
  if (result.length !== count || !result[0].startsWith('untrusted comment: ')) {
    throw new Error(`Invalid ${label} structure.`);
  }
  return result;
}

/** Check Tauri's base64-wrapped, prehashed minisign signature against the
 * app's embedded public key: the key id must match and the trusted comment
 * must be authentic. The package bytes are not hashed here; the Tauri updater
 * verifies them against this signature on the user's machine before installing.
 * No signer, private material, environment or network is used.
 */
export function verifyTauriSignature(filename, encodedSignature, encodedPublicKey) {
  const publicLines = lines(encodedPublicKey, 'updater public key', 2);
  const publicBytes = base64(publicLines[1], 'updater public key', 42);
  if (publicBytes.subarray(0, 2).toString('ascii') !== 'Ed') {
    throw new Error('Unsupported updater public key algorithm.');
  }
  const signatureLines = lines(encodedSignature, 'updater signature', 4);
  const signed = base64(signatureLines[1], 'updater signature', 74);
  if (signed.subarray(0, 2).toString('ascii') !== 'ED') {
    throw new Error('Only prehashed minisign update signatures are accepted.');
  }
  if (!signed.subarray(2, 10).equals(publicBytes.subarray(2, 10))) {
    throw new Error('Updater signature does not match the embedded public key.');
  }
  if (!signatureLines[2].startsWith('trusted comment: ')) {
    throw new Error('Updater signature has no trusted comment.');
  }
  const key = createPublicKey({
    key: Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), publicBytes.subarray(10)]),
    format: 'der', type: 'spki',
  });
  const comment = Buffer.from(signatureLines[2].slice('trusted comment: '.length), 'utf8');
  if (!verify(null, Buffer.concat([signed.subarray(10), comment]), key,
    base64(signatureLines[3], 'updater trusted-comment signature', 64))) {
    throw new Error('Updater trusted-comment signature verification failed.');
  }
  const stat = fs.lstatSync(filename);
  if (!stat.isFile()) throw new Error('Updater input must be a regular file.');
  return { bytes: stat.size };
}
