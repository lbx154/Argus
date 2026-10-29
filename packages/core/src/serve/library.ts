// The skill library in the web cockpit, read as it is on each request. The roles learn in their own
// turns, by writing skills; what the page shows of it is what they changed, the newest files under
// the state root's skills/. A plugin is a skill whose folder holds plugin.json ({ name, description,
// version, url }), installed and started by whoever wants it: Argus lists it and the page opens its
// url.

import { lstatSync, readFileSync, statSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { isJsonObject } from '@argus/contracts';
import { purpose, SKILL, SKILLS, skills, skillsHome } from '../prompts.js';
import type { Store } from '../store.js';
import { changed, under } from './files.js';
import { HttpError, type Handler } from './index.js';

export function library(store: Store): [string, RegExp, Handler][] {
  const plugins = () => [...skills(store.home)].map(([id, dir]) => [id, join(dir, 'plugin.json')] as const)
    .filter(([, file]) => lstatSync(file, { throwIfNoEntry: false })?.isFile()).map(([id, file]) => plugin(id, file));
  return [
    // Argus's own skills are the page's 'bundled' library, the roles' and the operator's its 'shared' one.
    ['GET', /^\/api\/skill-library$/, () => ({
      scopes: ['global'], verticals: [], active_vertical: '', errors: [],
      items: [...skills(store.home)].map(([name, dir]) => {
        const source = dirname(dir) === SKILLS ? 'bundled' : 'shared';
        return {
          library: source, source, scope: 'global', vertical: '', role: '', path: `${name}/${SKILL}`, name, description: purpose(dir),
          is_default: source === 'bundled', updated_at: source === 'bundled' ? null : statSync(join(dir, SKILL)).mtimeMs / 1000,
        };
      }),
    })],
    ['GET', /^\/api\/skill-library\/document$/, ({ url }) => {
      const name = url.searchParams.get('library');
      const root = name === 'bundled' ? SKILLS : name === 'shared' ? skillsHome(store.home) : null;
      const found = root && under(root, url.searchParams.get('path') ?? '');
      if (!found) throw new HttpError(404, 'no such skill file');
      return { content: readFileSync(found.file, 'utf8') };
    }],
    // The page reads the library again when the revision changes: when a role has changed a skill.
    ['GET', /^\/api\/projects\/[^/]+\/learning$/, () => {
      const home = skillsHome(store.home);
      return { jobs: [], pending: 0, revision: Math.max(statSync(home).mtimeMs, changed(home)[0]?.stat.mtimeMs ?? 0) };
    }],
    ['GET', /^\/api\/knowledge\/feed$/, ({ url }) => {
      const home = skillsHome(store.home);
      return {
        events: changed(home).slice(0, Number(url.searchParams.get('limit') ?? 50)).map(({ path, stat }) => ({
          ts: stat.mtimeMs / 1000, kind: 'learned', scope: 'global', vertical: '', path, source_project: '', role: '', note: '',
          title: basename(path) === SKILL ? purpose(join(home, dirname(path))) : path,
        })),
      };
    }],
    ['GET', /^\/api\/plugins$/, () => ({ plugins: plugins() })],
    ['POST', /^\/api\/plugins\/([^/]+)\/launch$/, ({ params: [id] }) => {
      const found = plugins().find(plugin => plugin.id === id);
      if (!found) throw new HttpError(404, `no plugin ${id}`);
      if (!found.url) throw new HttpError(400, found.reason, 'plugin_unavailable');
      return { url: found.url };
    }],
  ];
}

/** A plugin as the page's plugin center reads one: ready to open when its plugin.json names a url. */
function plugin(id: string, file: string) {
  let spec: unknown = null;
  try { spec = JSON.parse(readFileSync(file, 'utf8')); } catch { /* said in its reason */ }
  const text = (key: string): string => {
    const value = isJsonObject(spec) ? spec[key] : undefined;
    return typeof value === 'string' ? value : '';
  };
  // The page opens the url in itself: only a web address, never a script.
  const url = /^https?:\/\//i.test(text('url')) ? text('url') : '';
  const reason = !isJsonObject(spec) ? `${file} is not a JSON object.` : url ? '' : `${file} names no http(s) url to open.`;
  return {
    id, name: text('name') || id, description: text('description'), version: text('version'), url, reason,
  };
}
