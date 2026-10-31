// The operator's skill store. Both entry points install through this transaction; roles only read it.
// The operator may offer the team's own skills to it, which the host turns into a pull request.
import { lstatSync, readdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { mkdir, mkdtemp, rename, rm, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { packsHome, purpose, SKILL, SKILLS, skills, skillsHome } from './prompts.js';

const CATALOG = 'https://raw.githubusercontent.com/Argus-AiTeam/argus-verticals/main/packs/catalog.json';
const MIB = 2 ** 20;
export interface Pack {
  name: string; version: string; description: string; description_zh?: string;
  requires: string[]; needs: string[]; files: { path: string; size: number }[];
}
/** The version of the pack `value` installed under `home`; null when it is not. */
export function installed(home: string, value: string): string | null {
  const dir = resolve(packsHome(home), value);
  return skills(home).some(([, found]) => found === dir) ? purpose(dir, 'version') : null;
}
const NAME = /^[a-z0-9][a-z0-9_-]*$/;
function name(value: string): void {
  if (!NAME.test(value)) throw new Error('Invalid pack name');
}
async function download(url: string, limit: number): Promise<Buffer> {
  const response = await fetch(url, { redirect: 'error', signal: AbortSignal.timeout(30_000) });
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
/** Why this version cannot install a pack, or null when it can: the store's one rule, for an entry of
 * its catalog and for a folder offered to it alike. An entry it cannot install is left out of the
 * catalog, so that one entry, or a field added later, never takes the whole store down. */
function fault(pack: Pack): string | null {
  if (!pack || typeof pack.name !== 'string' || !NAME.test(pack.name)) return `A store name is lowercase letters, digits, - and _, not ${JSON.stringify(pack?.name)}`;
  if (lstatSync(resolve(SKILLS, pack.name), { throwIfNoEntry: false })) return `${pack.name} is the name of a skill Argus comes with`;
  if (typeof pack.description !== 'string' || !pack.description.trim()) return `The front matter of ${SKILL} must say what the skill is for (description)`;
  if (typeof pack.version !== 'string' || !pack.version.trim() || /[\r\n]/.test(pack.version)) return 'Invalid version';
  if ((pack.description_zh !== undefined && typeof pack.description_zh !== 'string')
    || !Array.isArray(pack.requires) || pack.requires.some(item => typeof item !== 'string' || !NAME.test(item))
    || !Array.isArray(pack.needs) || pack.needs.some(item => typeof item !== 'string') || !Array.isArray(pack.files)) return 'Invalid pack entry';
  const paths = new Set<string>();
  let total = 0;
  for (const file of pack.files) {
    if (!file || typeof file.path !== 'string' || file.path.includes('\\') || /[\x00-\x1f:]/.test(file.path)
      || file.path.split('/').some(part => !part || part.startsWith('.')) || paths.has(file.path)
      || !Number.isSafeInteger(file.size) || file.size < 0) return `The store cannot hold ${JSON.stringify(file?.path)}: no part of a path may start with a dot or hold \\, : or a control character`;
    paths.add(file.path);
    total += file.size;
  }
  if (!paths.has(SKILL)) return `A pack has a ${SKILL}`;
  return total > 8 * MIB ? `A pack holds at most 8 MiB; this one holds ${(total / MIB).toFixed(1)} MiB` : null;
}
export async function catalog(): Promise<Pack[]> {
  const data = JSON.parse((await download(CATALOG, MIB)).toString('utf8')) as { packs?: Pack[] };
  if (!Array.isArray(data.packs)) throw new Error('Invalid pack catalog');
  const names = new Set<string>();
  return data.packs.filter(pack => !fault(pack) && !names.has(pack.name) && names.add(pack.name));
}
export async function add(home: string, value: string): Promise<void> {
  name(value);
  const pack = (await catalog()).find(pack => pack.name === value);
  if (!pack) throw new Error(`No such pack: ${value}`);
  // Metadata is retained, but dependency installation is outside this first version.
  if (pack.requires.length) throw new Error('Pack dependencies are not supported');
  const target = resolve(packsHome(home), value);
  const stage = await mkdtemp(resolve(home, `skill-packs.tmp-${value}-`));
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
  }
}
export async function remove(home: string, value: string): Promise<void> {
  name(value);
  await rm(resolve(packsHome(home), value), { recursive: true, force: true });
}

/** The operator's offer of a team skill to the store, kept as contributions/<name>.json; the host opens
 * a pull request from it, which the store's maintainers review, and answers in the same file: the
 * pull request's `url`, or an `error`, at `submitted_at`. A host that does so makes contributions/;
 * without it no one would answer, so there is nothing to offer. */
export interface Contribution { name: string; version: string; requested_at: number; submitted_at?: number; url?: string; error?: string }
const offer = (home: string, value: string): string => resolve(home, 'contributions', `${value}.json`);
export const offered = (home: string): boolean => lstatSync(resolve(home, 'contributions'), { throwIfNoEntry: false })?.isDirectory() ?? false;
/** The folder's files, as the store would hold them: compiled Python left out, a link refused where it is met, never followed. */
function* held(dir: string, under = ''): Generator<{ path: string; size: number }> {
  for (const entry of readdirSync(join(dir, under), { withFileTypes: true })) {
    const path = under ? `${under}/${entry.name}` : entry.name;
    if (entry.name === '__pycache__' || entry.name.endsWith('.pyc')) continue;
    if (entry.isDirectory()) yield* held(dir, path);
    else if (entry.isFile()) yield { path, size: lstatSync(join(dir, path)).size };
    else throw new Refused(`${path} is a link or not a regular file; the store holds only regular files`);
  }
}
/** The offer of the team's skill `value`; null when there is none, or it cannot be read. */
export function contribution(home: string, value: string): Contribution | null {
  try { return JSON.parse(readFileSync(offer(home, value), 'utf8')) as Contribution; } catch { return null; }
}
/** A team skill the store would not take, with the reason, which the operator acts on. */
export class Refused extends Error {}
/** Offer the team's skill `value` to the store, once it meets the store's rule. Offering it again asks anew.
 * It runs synchronously and writes aside under its process's id, so no two offers ever share the file written aside. */
export function contribute(home: string, value: string): void {
  const dir = resolve(skillsHome(home), value);
  if (!skills(home).some(([, found]) => found === dir)) throw new Error(`No such team skill: ${value}`);
  if (!offered(home)) throw new Error('No host here opens pull requests to the skill store');
  const version = purpose(dir, 'version') || '0.1.0';
  const reason = fault({ name: value, version, description: purpose(dir), requires: [], needs: [], files: [...held(dir)] });
  if (reason) throw new Refused(reason);
  const file = offer(home, value);
  writeFileSync(`${file}.${process.pid}.tmp`, JSON.stringify({ name: value, version, requested_at: Date.now() / 1000 }));
  renameSync(`${file}.${process.pid}.tmp`, file);
}
