// The composer's draft of each project, in its directory (draft.json), so a reload or another
// device picks it up: its text and the files picked for it. A save names the revision it
// replaces; a save over one it never saw is refused, so no device overwrites another's unseen.

import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { isJsonObject } from '@argus/contracts';
import type { Project, Store } from '../store.js';
import { HttpError, type Handler } from './index.js';

const LIMIT = 128 * 1024;

export function drafts(store: Store, project: (id: string) => Project): [string, RegExp, Handler][] {
  const file = (id: string): string => join(store.dir(project(id).id), 'draft.json');
  const read = (path: string): { value: Record<string, unknown>; revision: number } => {
    try { return JSON.parse(readFileSync(path, 'utf8')); } catch { return { value: {}, revision: 0 }; }
  };
  return [
    ['GET', /^\/api\/projects\/([^/]+)\/browser-draft$/, ({ params: [id] }) => read(file(id!))],
    ['POST', /^\/api\/projects\/([^/]+)\/browser-draft$/, async ({ params: [id], body }) => {
      const path = file(id!);
      const { value, revision } = await body();
      if (!isJsonObject(value) || typeof revision !== 'number' || !Number.isSafeInteger(revision) || revision < 0) {
        throw new HttpError(400, 'a draft is saved as { value, revision }');
      }
      if (JSON.stringify(value).length > LIMIT) throw new HttpError(413, 'Draft metadata is too large');
      if (read(path).revision !== revision) throw new HttpError(409, 'Another device changed this draft. Choose which draft to keep.');
      const saved = { value, revision: revision + 1 };
      writeFileSync(path, `${JSON.stringify(saved)}\n`);
      return saved;
    }],
  ];
}
