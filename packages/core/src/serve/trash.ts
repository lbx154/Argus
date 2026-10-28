// Deleting a project moves its directory to $ARGUS_HOME/trash/<id>-<time>, from where it can be
// moved back; its workdir, where the work is, stays as it is.

import { mkdirSync, renameSync, statSync } from 'node:fs';
import { join } from 'node:path';
import type { Project, Store } from '../store.js';
import { HttpError, type Handler } from './index.js';
import type { Runs } from './runs.js';

export function trash(store: Store, runs: Runs, project: (id: string) => Project): [string, RegExp, Handler][] {
  return [
    ['DELETE', /^\/api\/projects\/([^/]+)$/, ({ params: [id] }) => {
      const { id: sid, workdir } = project(id!);
      runs.stop(sid);
      // The call it interrupts still records how it ended, into the project's directory.
      if (runs.live(sid).since !== null) throw new HttpError(409, 'The work is stopping; delete the project once it has stopped.');
      const to = join('trash', `${sid}-${new Date().toISOString().replace(/\D/g, '').slice(0, 14)}`);
      mkdirSync(join(store.home, 'trash'), { recursive: true });
      renameSync(store.dir(sid), join(store.home, to));
      return { ok: true, sid, trash_path: to, workdir, workdir_preserved: statSync(workdir, { throwIfNoEntry: false })?.isDirectory() ?? false };
    }],
  ];
}
