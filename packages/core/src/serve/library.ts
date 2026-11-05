// The skill library in the web cockpit, read as it is on each request. The roles learn in their own
// turns, by writing skills; what the page shows of it is what they changed, the newest files under
// the state root's skills/.

import { readFileSync, statSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { add, catalog, contribute, contribution, installed, offered, Refused, remove } from '../packs.js';
import { prepare } from '../packSetup.js';
import { packsHome, purpose, SKILL, SKILLS, skills, skillsHome, tools, toolsHome, wikiHome } from '../prompts.js';
import type { Store } from '../store.js';
import { changed, under } from './files.js';
import { HttpError, type Handler } from './index.js';

export function library(store: Store): [string, RegExp, Handler][] {
  return [
    ['GET', /^\/api\/skill-packs$/, async () => {
      // The catalog is on GitHub: when it cannot be read, say so rather than fail as Argus's own error.
      const packs = await catalog().catch(error => { throw new HttpError(502, `The skill store is unavailable: ${error instanceof Error ? error.message : error}`); });
      return { packs: packs.map(pack => ({ ...pack, installed_version: installed(store.home, pack.name) })) };
    }],
    // The operator's consent to offer a team skill to the store is kept; the host opens the pull request.
    // What the store's rule refuses is told by its code, so the page shows that reason and no other error's words.
    ...([['skill-packs/add', add], ['skill-packs/remove', remove], ['skill-library/contribute', contribute]] as const).map(([path, action]) => ['POST', new RegExp(`^/api/${path}$`), async ({ body }) => {
      const { name } = await body();
      if (typeof name !== 'string') throw new HttpError(400, 'Name required');
      try { await action(store.home, name); }
      catch (error) { throw new HttpError(400, error instanceof Error ? error.message : String(error), error instanceof Refused ? 'not_for_store' : ''); }
      // A pack just installed readies its tools now, not at its first role's turn.
      if (action === add) void prepare(store.home);
      return { ok: true };
    }] as [string, RegExp, Handler]),
    // Argus's own skills are the page's 'bundled' library, the roles' and the operator's its 'shared' one;
    // a team skill can be offered to the store only where a host answers offers.
    ['GET', /^\/api\/skill-library$/, () => {
      const host = offered(store.home);
      return {
        items: [...skills(store.home)].map(([name, dir]) => {
          const source = dirname(dir) === SKILLS ? 'bundled' : dirname(dir) === packsHome(store.home) ? 'store' : 'shared';
          return {
            library: source, source, path: `${name}/${SKILL}`, name, description: purpose(dir), version: purpose(dir, 'version'),
            updated_at: source === 'bundled' ? null : statSync(join(dir, SKILL)).mtimeMs / 1000,
            ...source === 'shared' && host ? { contribution: contribution(store.home, name) } : {},
          };
        }),
      };
    }],
    ['GET', /^\/api\/skill-library\/document$/, ({ url }) => {
      const name = url.searchParams.get('library');
      const root = name === 'bundled' ? SKILLS : name === 'shared' ? skillsHome(store.home) : name === 'store' ? packsHome(store.home) : name === 'wiki' ? wikiHome(store.home) : name === 'tools' ? toolsHome(store.home) : null;
      const found = root && under(root, url.searchParams.get('path') ?? '');
      if (!found) throw new HttpError(404, 'no such skill file');
      return { content: readFileSync(found.file, 'utf8') };
    }],
    // What the team learned lately: its skills' files, its wiki's pages and its tools, newest first.
    ['GET', /^\/api\/knowledge\/feed$/, ({ url }) => {
      const home = skillsHome(store.home);
      const skillEvents = changed(home).map(({ path, stat }) => ({
        ts: stat.mtimeMs / 1000, path, title: basename(path) === SKILL ? purpose(join(home, dirname(path))) : path,
      }));
      const wiki = wikiHome(store.home);
      const pages = changed(wiki).filter(({ path }) => path.endsWith('.md')).map(({ file, path, stat }) => ({
        ts: stat.mtimeMs / 1000, path: `wiki/${path}`, title: /^#\s+(.+)$/m.exec(readFileSync(file, 'utf8'))?.[1]?.trim() || path,
      }));
      const made = tools(store.home).map(([name, said]) => ({
        ts: statSync(join(toolsHome(store.home), name)).mtimeMs / 1000, path: `tools/${name}`, title: said || name,
      }));
      return { events: [...skillEvents, ...pages, ...made].sort((a, b) => b.ts - a.ts).slice(0, Number(url.searchParams.get('limit') ?? 50)) };
    }],
  ];
}
