// The starter library: the skills, wiki pages and tools every operator's Argus begins with, shipped
// in the package's starter/. On a start, each item the operator's library was never given is copied
// in once; from then on it is theirs, to improve or delete like anything the roles write. An upgrade
// that adds to the starter library adds only what is new, and an item the operator deleted stays
// deleted, because what was given is recorded.

import { cpSync, existsSync, lstatSync, readdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { sharedHome, skillsHome, toolsHome, WIKI_INDEX, wikiHome } from './prompts.js';

export const STARTER = fileURLToPath(new URL('../starter', import.meta.url));
const RECORD = '.starter.json';
const KINDS = { skills: skillsHome, wiki: wikiHome, tools: toolsHome } as const;

/** Give the operator's library what of the starter library it was never given; returns what was added. */
export function seed(home: string, starter = STARTER): string[] {
  const record = join(sharedHome(home), RECORD);
  let given: string[] = [];
  try { given = (JSON.parse(readFileSync(record, 'utf8')) as { given?: string[] }).given ?? []; } catch { /* none yet */ }
  const added: string[] = [];
  const pages: string[] = [];
  for (const [kind, place] of Object.entries(KINDS)) {
    const from = join(starter, kind);
    if (!existsSync(from)) continue;
    const to = place(home);
    for (const name of readdirSync(from).sort()) {
      const key = `${kind}/${name}`;
      if (given.includes(key) || (kind === 'wiki' && name === WIKI_INDEX)) continue;
      given.push(key);
      // One of the same name is the operator's: theirs stands.
      if (lstatSync(join(to, name), { throwIfNoEntry: false })) continue;
      cpSync(join(from, name), join(to, name), { recursive: true, preserveTimestamps: true });
      added.push(key);
      if (kind === 'wiki') pages.push(name);
    }
  }
  // The wiki's index gains a line for each page given, as the starter's index words it.
  if (pages.length) {
    const index = resolve(wikiHome(home), WIKI_INDEX);
    const lines = readFileSync(join(starter, 'wiki', WIKI_INDEX), 'utf8').split('\n');
    const text = readFileSync(index, 'utf8');
    const more = pages.flatMap(page => lines.filter(line => line.includes(`](${page})`)));
    if (more.length) writeFileSync(index, `${text.replace(/\n*$/, '\n\n')}${more.join('\n')}\n`);
  }
  writeFileSync(`${record}.${process.pid}.tmp`, `${JSON.stringify({ given }, null, 1)}\n`);
  renameSync(`${record}.${process.pid}.tmp`, record);
  return added;
}
