// What the operator sets on a project: its directory, its name, and the backend, model and
// reasoning effort of all its roles; and across projects: the sidebar's pins and archive, kept in
// $ARGUS_HOME/browser-state.json, the trash a deleted project's directory goes to, and what each
// project has spent. A project's record is changed only while nothing is under way on it, since
// the work under way holds the record and writes it back.

import { readdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import type { ProjectCostRow } from '@argus/contracts';
import type { Project, Store } from '../store.js';
import { BACKENDS, ROLES, type Backend } from '../types.js';
import { HttpError, type Handler } from './index.js';
import type { Runs } from './runs.js';
import { daemon, projectRow } from './views.js';

/** The optional API capabilities these routes give the page. */
export const CAPABILITIES = ['project.workdir.v1', 'project.cost-feed.v1'];

/** The settings the page writes, by the Python runtime's names for them: a project has one
 * backend, model and effort, which every role uses. A model or effort is the backend's own word
 * for it; '' or `auto`, the backend's default. */
const SETTINGS: Record<string, (work: Project, value: string) => void> = {
  // A backend session is the backend's own: every role starts afresh on another.
  ARGUS_SKILL_RUNNER_BACKEND: (work, backend) => {
    if (!BACKENDS.includes(backend as Backend)) throw new HttpError(400, `the backend must be one of ${BACKENDS.join(', ')}`);
    if (backend !== work.backend) Object.assign(work, { backend, sessions: {} });
  },
  ARGUS_SKILL_MODEL: (work, model) => { work.model = model || null; },
  ...Object.fromEntries(ROLES.map(role => [`ARGUS_SKILL_${role.toUpperCase()}_REASONING_EFFORT`, (work: Project, effort: string) => {
    if (effort) work.effort = effort;
    else delete work.effort;
  }])),
};

/** The directory a request names, `~` for home; '' when it names none. It must exist. */
export function directory(asked: string): string {
  const path = asked && resolve(asked.replace(/^~(?=$|\/)/, homedir()));
  if (path && !statSync(path, { throwIfNoEntry: false })?.isDirectory()) throw new HttpError(400, `workdir is not a directory: ${path}`);
  return path;
}

const MARKS = ['pinned', 'archived', 'followup'];

export function projectRoutes(store: Store, runs: Runs, project: (id: string) => Project): [string, RegExp, Handler][] {
  const edit = (id: string, change: (work: Project) => void): void => {
    const work = project(id);
    if (runs.live(work.id).since !== null) throw new HttpError(409, 'Work is under way on this project: stop it first.');
    change(work);
    store.save(work);
  };
  const browserState = join(store.home, 'browser-state.json');
  const kept = (): { organization?: { value: Record<string, Record<string, boolean>>; revision: number } } => {
    try { return JSON.parse(readFileSync(browserState, 'utf8')); } catch { return {}; }
  };
  const trash = join(store.home, 'trash');
  /** Each project in the trash, as deleting it left it: its directory, named `<id>-<when>`. */
  const trashed = () => {
    let names: string[];
    try { names = readdirSync(trash); } catch { return []; }
    return names.flatMap(name => {
      try {
        const work = JSON.parse(readFileSync(join(trash, name, 'project.json'), 'utf8')) as Project;
        return [{
          trash_id: name, sid: work.id, label: work.name || work.request.split('\n')[0] || work.id, launch_cwd: work.workdir,
          // Moving the directory there is the last change to it.
          trash_path: `trash/${name}`, trashed_at: statSync(join(trash, name)).ctimeMs / 1000,
        }];
      } catch { return []; }
    }).sort((a, b) => b.trashed_at - a.trashed_at);
  };

  return [
    ['POST', /^\/api\/projects\/([^/]+)\/workdir$/, async ({ params: [id], body }) => {
      const workdir = directory(String((await body()).workdir ?? ''));
      if (!workdir) throw new HttpError(400, 'workdir must be an existing directory');
      // A backend session belongs to the directory it worked in: every role starts afresh in the new one.
      edit(id!, work => { if (work.workdir !== workdir) Object.assign(work, { workdir, sessions: {} }); });
      return { ok: true, workdir };
    }],
    // The page asks every few seconds; the project list carries the same figures.
    ['GET', /^\/api\/projects\/costs$/, () => ({
      projects: store.list().map((work): ProjectCostRow => {
        const { id, spend_usd = null, known_cost_usd = 0, spend_status = 'empty', usage_calls = 0, last_active } = projectRow(store, work, runs.live(work.id));
        return { id, spend_usd, known_cost_usd, spend_status, usage_calls, premium_requests: 0, updated_at: last_active };
      }),
      generated_at: Date.now() / 1000,
    })],
    ['PATCH', /^\/api\/projects\/([^/]+)$/, async ({ params: [id], body }) => {
      const name = String((await body()).name ?? '').replace(/\s+/g, ' ').trim().slice(0, 80);
      edit(id!, work => { if (name) work.name = name; else delete work.name; });
      return { ok: true, sid: id, name };
    }],
    ['GET', /^\/api\/browser-state\/organization$/, () => kept().organization ?? { value: {}, revision: 0 }],
    ['POST', /^\/api\/browser-state\/organization$/, async ({ body }) => {
      const { sid, field, value } = await body();
      const { id } = project(String(sid));
      if (!MARKS.includes(String(field)) || typeof value !== 'boolean') throw new HttpError(400, `a mark is one of ${MARKS.join(', ')}, true or false`);
      const state = kept();
      const { value: marks, revision } = state.organization ?? { value: {}, revision: 0 };
      marks[id] = { pinned: false, archived: false, followup: false, ...marks[id], [String(field)]: value };
      state.organization = { value: marks, revision: revision + 1 };
      writeFileSync(`${browserState}.tmp`, JSON.stringify(state));
      renameSync(`${browserState}.tmp`, browserState);
      return state.organization;
    }],
    ['GET', /^\/api\/trash$/, ({ url }) => {
      const query = (url.searchParams.get('query') ?? '').trim().toLowerCase();
      const [offset, limit] = [Number(url.searchParams.get('offset')) || 0, Number(url.searchParams.get('limit')) || 100];
      const entries = trashed().filter(entry => [entry.sid, entry.label, entry.launch_cwd, entry.trash_path].join(' ').toLowerCase().includes(query));
      return { entries: entries.slice(offset, offset + limit), total: entries.length, offset, limit };
    }],
    ['POST', /^\/api\/trash\/([^/]+)\/restore$/, ({ params: [name] }) => {
      const entry = trashed().find(entry => entry.trash_id === name);
      if (!entry || !/^p-[0-9a-f]+$/.test(entry.sid)) throw new HttpError(404, 'unknown trash entry');
      if (statSync(store.dir(entry.sid), { throwIfNoEntry: false })) throw new HttpError(409, 'a project with this id already exists');
      renameSync(join(trash, name!), store.dir(entry.sid));
      return { ok: true, sid: entry.sid };
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/config$/, ({ params: [id] }) => {
      const work = project(id!);
      const { backend, model, effort } = work;
      const { backend_label } = daemon(work, runs.live(work.id));
      const source = (value: unknown) => value ? 'persisted' : 'default';
      const knob = (name: string, value: string) => ({ name, group: 'Runtime', value, source: source(value), saved: value, default: '', doc: '' });
      return {
        schema_version: 1, generated_at_utc: new Date().toISOString(),
        roles: ROLES.map(role => ({
          role, backend, backend_label, backend_source: 'persisted', model: model ?? '', model_source: source(model),
          reasoning_effort: effort ?? null, reasoning_effort_source: source(effort), description: '',
        })),
        operator_knobs: [knob('ARGUS_SKILL_RUNNER_BACKEND', backend), knob('ARGUS_SKILL_MODEL', model ?? '')],
        model_options: model ? [{ model, source: 'current' }] : [], how_to_change: [],
      };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/config\/set$/, async ({ params: [id], body }) => {
      const { name, value } = await body();
      const set = SETTINGS[String(name ?? '').trim().toUpperCase()];
      if (!set) throw new HttpError(400, `${String(name)} is not a setting of this project: it has one backend, model and effort, for every role`);
      const given = String(value ?? '').trim();
      const word = /^(auto|default)?$/i.test(given) ? '' : given;
      // Given to the backend's command line as one argument: never an option of its own.
      if (word && !/^\w[\w.:/@+[\]-]*$/.test(word)) throw new HttpError(400, `not a name a backend takes: ${given}`);
      edit(id!, work => set(work, word));
      return { name, value: word };
    }],
    // The models to choose from are the backend's; the page offers the one set, and takes any other by name.
    ['GET', /^\/api\/projects\/([^/]+)\/model-choices$/, ({ params: [id] }) => {
      const { backend, model } = project(id!);
      return {
        backend, billing: 'token', premium_request_usd: null, task_sample: 0,
        options: (model ? [model] : []).map(model => ({
          model, source: 'current', default: false, invalid: false, price: null, task_estimate_usd: null, latency_ms: null, latency_samples: 0,
          availability: { state: 'unknown', at: 0, reason: '' },
        })),
      };
    }],
  ];
}
