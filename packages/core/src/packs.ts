// The operator's skill store. Both entry points install through this transaction; roles only read it.
import { mkdirSync, lstatSync } from 'node:fs';
import { mkdir, mkdtemp, rename, rm, rmdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { purpose, SKILL, SKILLS } from './prompts.js';

const CATALOG = 'https://raw.githubusercontent.com/Argus-AiTeam/argus-verticals/main/packs/catalog.json';
const MIB = 2 ** 20;
export interface Pack {
  name: string; version: string; description: string; description_zh?: string;
  requires: string[]; needs: string[]; files: { path: string; size: number }[];
}
export function packsHome(home: string): string {
  const dir = resolve(home, 'skill-packs');
  mkdirSync(dir, { recursive: true });
  return dir;
}
function name(value: string): void {
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(value)) throw new Error('Invalid pack name');
  if (lstatSync(resolve(SKILLS, value), { throwIfNoEntry: false })) throw new Error(`Pack conflicts with bundled skill: ${value}`);
}
async function download(url: string, limit: number): Promise<Buffer> {
  const response = await fetch(url, { redirect: 'error' });
  if (!response.ok) { await response.body?.cancel(); throw new Error(`Download failed: HTTP ${response.status}`); }
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Download has no body');
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > limit) throw new Error('Download exceeds size limit');
      chunks.push(chunk.value);
    }
    return Buffer.concat(chunks);
  } finally { await reader.cancel(); }
}
export async function catalog(): Promise<Pack[]> {
  const data = JSON.parse((await download(CATALOG, MIB)).toString('utf8')) as { packs?: Pack[] };
  if (!Array.isArray(data.packs)) throw new Error('Invalid pack catalog');
  const names = new Set<string>();
  for (const pack of data.packs) {
    if (!pack || typeof pack.name !== 'string') throw new Error('Invalid pack metadata');
    name(pack.name);
    if (names.has(pack.name)) throw new Error('Duplicate pack name');
    names.add(pack.name);
    if (Object.keys(pack).some(key => !['name', 'version', 'description', 'description_zh', 'requires', 'needs', 'files'].includes(key))
      || typeof pack.version !== 'string' || !pack.version.trim() || /[\r\n]/.test(pack.version)
      || typeof pack.description !== 'string' || (pack.description_zh !== undefined && typeof pack.description_zh !== 'string')
      || !Array.isArray(pack.requires) || pack.requires.some(item => typeof item !== 'string' || !/^[a-z0-9][a-z0-9_-]*$/.test(item))
      || !Array.isArray(pack.needs) || pack.needs.some(item => typeof item !== 'string') || !Array.isArray(pack.files)) throw new Error('Invalid pack metadata');
    const paths = new Set<string>();
    let total = 0;
    for (const file of pack.files) {
      if (!file || typeof file.path !== 'string' || file.path.includes('\\') || /[\x00-\x1f:]/.test(file.path)
        || file.path.split('/').some(part => !part || part.startsWith('.')) || paths.has(file.path)
        || !Number.isSafeInteger(file.size) || file.size < 0 || Object.keys(file).some(key => !['path', 'size'].includes(key))) throw new Error('Invalid pack file');
      paths.add(file.path);
      total += file.size;
    }
    if (!paths.has(SKILL) || total > 8 * MIB) throw new Error('Pack must contain SKILL.md and fit within 8 MiB');
  }
  return data.packs;
}
export async function add(home: string, value: string): Promise<void> {
  name(value);
  const pack = (await catalog()).find(pack => pack.name === value);
  if (!pack) throw new Error(`No such pack: ${value}`);
  // Metadata is retained, but dependency installation is outside this first version.
  if (pack.requires.length) throw new Error('Pack dependencies are not supported');
  const target = resolve(packsHome(home), value);
  const stagingRoot = resolve(home, 'skill-packs.tmp');
  await mkdir(stagingRoot, { recursive: true });
  const stage = await mkdtemp(resolve(stagingRoot, `${value}-`));
  const fresh = resolve(stage, 'new');
  const old = resolve(stage, 'old');
  let moved = false;
  try {
    await mkdir(fresh);
    for (const file of pack.files) {
      const bytes = await download(new URL(`${pack.name}/${file.path.split('/').map(encodeURIComponent).join('/')}`, CATALOG).href, file.size);
      if (bytes.length !== file.size) throw new Error(`File size mismatch: ${file.path}`);
      const destination = resolve(fresh, file.path);
      await mkdir(dirname(destination), { recursive: true });
      await writeFile(destination, bytes);
    }
    if (purpose(fresh, 'version') !== pack.version || !purpose(fresh)) throw new Error('SKILL.md must describe the skill and match the catalog version');
    if (lstatSync(target, { throwIfNoEntry: false })) { await rename(target, old); moved = true; }
    try { await rename(fresh, target); }
    catch (error) { if (moved) await rename(old, target); throw error; }
  } finally {
    await rm(stage, { recursive: true, force: true });
    // Other concurrent transactions may still be using the parent.
    await rmdir(stagingRoot).catch(error => { if (error.code !== 'ENOTEMPTY' && error.code !== 'ENOENT') throw error; });
  }
}
export async function remove(home: string, value: string): Promise<void> {
  name(value);
  await rm(resolve(packsHome(home), value), { recursive: true, force: true });
}
