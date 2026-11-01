// Bundle the build machine's verified Node distribution, with its redistribution license.
// No installer, global environment change, downloads or signing material are involved.
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('The bundled runtime is for Windows x64 builds.');
const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || major === 22 && minor < 19) throw new Error('Use Node 22.19+ to build the desktop runtime.');
const directory = resolve(dirname(fileURLToPath(import.meta.url)), '../resources/node-runtime');
const source = process.execPath;
const license = join(dirname(source), 'LICENSE');
if (!existsSync(license)) throw new Error('The Node distribution LICENSE is required beside node.exe. Use the official Node distribution.');
mkdirSync(directory, { recursive: true });
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');
for (const [input, name] of [[source, 'node.exe'], [license, 'LICENSE']]) {
  const target = join(directory, name);
  if (existsSync(target) && hash(target) !== hash(input)) throw new Error(`A different runtime already exists at ${target}; do not overwrite it without review.`);
  if (!existsSync(target)) copyFileSync(input, target);
}
const manifest = join(directory, 'runtime.json');
const contents = JSON.stringify({ name: 'Node.js', version: process.versions.node, platform: 'win32', arch: 'x64', sha256: hash(source) }, null, 2) + '\n';
if (!existsSync(manifest)) writeFileSync(manifest, contents, { flag: 'wx' });
else if (readFileSync(manifest, 'utf8') !== contents) throw new Error('Bundled runtime manifest disagrees with this build.');
console.log(`Bundled Node ${process.versions.node}: ${directory}`);
