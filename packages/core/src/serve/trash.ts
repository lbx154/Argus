// Deleting a project moves its directory to $ARGUS_HOME/trash/<id>-<time>, from where it can be
// moved back; its workdir, where the work is, stays as it is.

import { mkdirSync, readdirSync, readFileSync, renameSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { PROJECT_ID, type Project, type Store } from '../store.js';
import { HttpError, type Handler } from './index.js';
import type { Runs } from './runs.js';
import { named } from './views.js';

export function trash(store: Store, runs: Runs, project: (id: string) => Project): [string, RegExp, Handler][] {
  const bin = join(store.home, 'trash');
  /** Each project in the trash, read from its project.json; moving it there was the last change to its directory. */
  const trashed = () => {
    let names: string[];
    try { names = readdirSync(bin); } catch { return []; }
    return names.flatMap(name => {
      try {
        const work = JSON.parse(readFileSync(join(bin, name, 'project.json'), 'utf8')) as Project;
        return [{
          trash_id: name, sid: work.id, label: named(work) || work.id, launch_cwd: work.workdir,
          trash_path: join('trash', name), trashed_at: statSync(join(bin, name)).ctimeMs / 1000,
        }];
      } catch { return []; }
    }).sort((a, b) => b.trashed_at - a.trashed_at);
  };
  return [
    ['DELETE', /^\/api\/projects\/([^/]+)$/, ({ params: [id] }) => {
      const { id: sid, workdir } = project(id!);
      runs.stop(sid);
      // The call it interrupts still records how it ended, into the project's directory.
      if (runs.live(sid).since !== null) throw new HttpError(409, 'The work is stopping; delete the project once it has stopped.', 'work_stopping');
      const to = join('trash', `${sid}-${new Date().toISOString().replace(/\D/g, '').slice(0, 14)}`);
      mkdirSync(bin, { recursive: true });
      renameSync(store.dir(sid), join(store.home, to));
      runs.changed(sid);
      return { ok: true, sid, trash_path: to, workdir, workdir_preserved: statSync(workdir, { throwIfNoEntry: false })?.isDirectory() ?? false };
    }],
    ['GET', /^\/api\/trash$/, ({ url }) => {
      const query = (url.searchParams.get('query') ?? '').trim().toLowerCase();
      const [offset, limit] = [Number(url.searchParams.get('offset')) || 0, Number(url.searchParams.get('limit')) || 100];
      const entries = trashed().filter(entry => [entry.sid, entry.label, entry.launch_cwd, entry.trash_path].join(' ').toLowerCase().includes(query));
      return { entries: entries.slice(offset, offset + limit), total: entries.length, offset, limit };
    }],
    ['POST', /^\/api\/trash\/([^/]+)\/restore$/, ({ params: [name] }) => {
      const entry = trashed().find(entry => entry.trash_id === name);
      if (!entry || !PROJECT_ID.test(entry.sid)) throw new HttpError(404, 'unknown trash entry');
      if (statSync(store.dir(entry.sid), { throwIfNoEntry: false })) throw new HttpError(409, 'a project with this id already exists', 'project_exists');
      renameSync(join(bin, name!), store.dir(entry.sid));
      runs.changed(entry.sid);
      return { ok: true, sid: entry.sid };
    }],
  ];
}
