// What the operator sets on a project: its directory. A project's record is changed only while
// nothing is under way on it, since the work under way holds the record and writes it back.

import { statSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve } from 'node:path';
import type { Project, Store } from '../store.js';
import { HttpError, type Handler } from './index.js';
import type { Runs } from './runs.js';

/** The optional API capabilities these routes give the page. */
export const CAPABILITIES = ['project.workdir.v1'];

/** The directory a request names, `~` for home; '' when it names none. It must exist. */
export function directory(asked: string): string {
  const path = asked && resolve(asked.replace(/^~(?=$|\/)/, homedir()));
  if (path && !statSync(path, { throwIfNoEntry: false })?.isDirectory()) throw new HttpError(400, `workdir is not a directory: ${path}`);
  return path;
}

export function projectRoutes(store: Store, runs: Runs, project: (id: string) => Project): [string, RegExp, Handler][] {
  const edit = (id: string, change: (work: Project) => void): void => {
    const work = project(id);
    if (runs.live(work.id).since !== null) throw new HttpError(409, 'Work is under way on this project: stop it first.');
    change(work);
    store.save(work);
  };

  return [
    ['POST', /^\/api\/projects\/([^/]+)\/workdir$/, async ({ params: [id], body }) => {
      const workdir = directory(String((await body()).workdir ?? ''));
      if (!workdir) throw new HttpError(400, 'workdir must be an existing directory');
      // A backend session belongs to the directory it worked in: every role starts afresh in the new one.
      edit(id!, work => { if (work.workdir !== workdir) Object.assign(work, { workdir, sessions: {} }); });
      return { ok: true, workdir };
    }],
  ];
}
