// What the operator sets on a project: its directory, its name, the backend, model and reasoning
// effort of all its roles, and a fresh Manager session; and across projects: what each has spent,
// and the sidebar's pins and archive, kept in $ARGUS_HOME/browser-state.json. A project's record is
// changed where it is held: by the work under way on it, or on disk. Its directory and backend
// change only while nothing is under way, since the calls under way work in them.

import { accessSync, constants, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import type { ProjectCostRow } from '@argus/contracts';
import { modelPriceFor } from '@argus/runtime';
import type { Project, Store } from '../store.js';
import { BACKENDS, ROLES, type Backend } from '../types.js';
import { HttpError, type Handler } from './index.js';
import type { Runs } from './runs.js';
import { BACKEND_LABELS, projectRow } from './views.js';

/** The optional API capabilities these routes give the page. */
export const CAPABILITIES = ['project.workdir.v1', 'project.config.v1', 'project.cost-feed.v1', 'project.editions.v1'];

/** The settings the page writes: a project has one backend, model and effort, which every role
 * uses. A model or effort is the backend's own word for it; '' or `auto`, the backend's default. */
const SETTINGS: Record<string, (work: Project, value: string) => void> = {
  // A backend session is the backend's own: every role starts afresh on another.
  ARGUS_SKILL_RUNNER_BACKEND: (work, backend) => {
    if (!BACKENDS.includes(backend as Backend)) throw new HttpError(400, `the backend must be one of ${BACKENDS.join(', ')}`, 'invalid_backend');
    if (backend !== work.backend) Object.assign(work, { backend, sessions: {} });
  },
  ARGUS_SKILL_MODEL: (work, model) => { work.model = model || null; },
  ARGUS_SKILL_REASONING_EFFORT: (work, effort) => {
    if (effort) work.effort = effort;
    else delete work.effort;
  },
};

/** The directory a request names, `~` for home, made if it is new; '' when it names none. */
export function directory(asked: string): string {
  const path = asked && resolve(asked.replace(/^~(?=$|\/)/, homedir()));
  try {
    // Each missing folder in turn, from the nearest that exists: Node's recursive mkdir never returns
    // for a path under /proc. A root that does not exist (a drive, a share) is its own parent.
    const missing: string[] = [];
    for (let dir = path, below = ''; dir !== below && !existsSync(dir); below = dir, dir = dirname(dir)) missing.unshift(dir);
    for (const dir of missing) mkdirSync(dir);
    if (path && !statSync(path).isDirectory()) throw new HttpError(400, `${path} is a file, not a directory`, 'workdir_not_directory');
    if (path) accessSync(path, constants.R_OK | constants.W_OK | constants.X_OK);
  } catch (error) {
    if (error instanceof HttpError) throw error;
    const reason = (error as NodeJS.ErrnoException).code;
    const code = reason === 'EACCES' || reason === 'EPERM' || reason === 'EROFS' ? 'workdir_permission_denied'
      : reason === 'ENOTDIR' ? 'workdir_not_directory' : reason === 'ENOENT' ? 'workdir_parent_missing' : 'invalid_workdir';
    throw new HttpError(400, `${path} cannot be the working directory: ${error instanceof Error ? error.message : String(error)}`, code);
  }
  return path;
}

const MARKS = ['pinned', 'archived', 'followup'];

export function projectRoutes(store: Store, runs: Runs, project: (id: string) => Project): [string, RegExp, Handler][] {
  const edit = (id: string, change: (work: Project) => void, idle = false): void => {
    const { id: sid } = project(id);
    if (idle && runs.live(sid).since !== null) throw new HttpError(409, 'Work is under way on this project: stop it first.', 'work_in_progress');
    runs.edit(sid, change);
  };
  const browserState = join(store.home, 'browser-state.json');
  const kept = (): { organization?: { value: Record<string, Record<string, boolean>>; revision: number } } => {
    try { return JSON.parse(readFileSync(browserState, 'utf8')); } catch { return {}; }
  };

  return [
    ['POST', /^\/api\/projects\/([^/]+)\/workdir$/, async ({ params: [id], body }) => {
      const workdir = directory(String((await body()).workdir ?? ''));
      if (!workdir) throw new HttpError(400, 'workdir must be an existing directory', 'invalid_workdir');
      // A backend session belongs to the directory it worked in: every role starts afresh in the new one.
      edit(id!, work => { if (work.workdir !== workdir) Object.assign(work, { workdir, sessions: {} }); }, true);
      return { ok: true, workdir };
    }],
    ['PATCH', /^\/api\/projects\/([^/]+)$/, async ({ params: [id], body }) => {
      const name = String((await body()).name ?? '').replace(/\s+/g, ' ').trim().slice(0, 80);
      edit(id!, work => { if (name) work.name = name; else delete work.name; });
      return { ok: true, sid: id, name };
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/config$/, ({ params: [id] }) => {
      const { backend, model, effort } = project(id!);
      const source = (value: unknown) => value ? 'persisted' : 'default';
      const knob = (name: string, value: string) => ({ name, group: 'Runtime', value, source: source(value), saved: value, default: '', doc: '' });
      return {
        schema_version: 1, generated_at_utc: new Date().toISOString(),
        roles: ROLES.map(role => ({
          role, backend, backend_label: BACKEND_LABELS[backend], backend_source: 'persisted', model: model ?? '', model_source: source(model),
          reasoning_effort: effort ?? null, reasoning_effort_source: source(effort), description: '',
        })),
        operator_knobs: [knob('ARGUS_SKILL_RUNNER_BACKEND', backend), knob('ARGUS_SKILL_MODEL', model ?? '')],
        how_to_change: [], backends: BACKEND_LABELS,
      };
    }],
    // A model tried with a short turn of its own, before the operator picks it.
    ['POST', /^\/api\/projects\/([^/]+)\/model-choices\/probe$/, async ({ params: [id], body }) => {
      const model = String((await body()).model ?? '').trim();
      const { id: sid, backend, workdir, effort } = project(id!);
      const dir = store.dir(sid);
      const started = Date.now();
      const { completed, error } = await runs.callAgent({
        backend, role: 'manager', form: 'manager', access: 'read', cwd: workdir, prompt: 'Write to the operator the one word: ok.',
        model, ...effort ? { effort } : {}, callsDir: join(dir, 'calls'), usageFile: join(dir, 'usage.jsonl'),
      }).catch((thrown: unknown) => ({ completed: false, error: thrown instanceof Error ? thrown.message : String(thrown) }));
      return { model, ok: completed, error: completed ? '' : error ?? '', duration_ms: Date.now() - started };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/config\/set$/, async ({ params: [id], body }) => {
      const { name, value } = await body();
      const set = SETTINGS[String(name ?? '').trim().toUpperCase()];
      if (!set) throw new HttpError(400, `${String(name)} is not a setting of this project: it has one backend, model and effort, for every role`, 'invalid_setting');
      const given = String(value ?? '').trim();
      const word = /^(auto|default)?$/i.test(given) ? '' : given;
      // Given to the backend's command line as one argument: never an option of its own.
      if (word && !/^\w[\w.:/@+[\]-]*$/.test(word)) throw new HttpError(400, `not a name a backend takes: ${given}`, 'invalid_model');
      edit(id!, work => set(work, word), set === SETTINGS.ARGUS_SKILL_RUNNER_BACKEND);
      return { name, value: word };
    }],
    // The models to choose from are those the backend names, and the one set if it names it not;
    // the page takes any other by name.
    ['GET', /^\/api\/projects\/([^/]+)\/model-choices$/, async ({ params: [id] }) => {
      const { backend, model, workdir } = project(id!);
      const offered = await runs.callAgent.models?.(backend, workdir) ?? [];
      const named = model && !offered.some(row => row.model === model) ? [...offered, { model, default: false }] : offered;
      return {
        backend,
        options: named.map(row => {
          const price = modelPriceFor(row.model);
          return { model: row.model, default: row.default, price: price && { input: price.input_usd_per_mtok, output: price.output_usd_per_mtok } };
        }),
      };
    }],
    // The Manager's next turn starts a new session; the record it reads stays as it is.
    ['POST', /^\/api\/projects\/([^/]+)\/reset$/, ({ params: [id] }) => {
      edit(id!, work => { delete work.sessions.manager; });
      return { ok: true };
    }],
    // The page asks every few seconds; the project list carries the same figures.
    ['GET', /^\/api\/projects\/costs$/, () => ({
      projects: store.list().map((work): ProjectCostRow => {
        const { id, spend_usd = null, known_cost_usd = 0, spend_status = 'empty', usage_calls = 0, last_active } =
          projectRow(store, work, runs.live(work.id));
        return { id, spend_usd, known_cost_usd, spend_status, usage_calls, premium_requests: 0, updated_at: last_active };
      }),
      generated_at: Date.now() / 1000,
    })],
    ['GET', /^\/api\/browser-state\/organization$/, () => kept().organization ?? { value: {}, revision: 0 }],
    ['POST', /^\/api\/browser-state\/organization$/, async ({ body }) => {
      const { sid, field, value } = await body();
      const { id } = project(String(sid));
      if (!MARKS.includes(String(field)) || typeof value !== 'boolean') throw new HttpError(400, `a mark is one of ${MARKS.join(', ')}, true or false`, 'invalid_request');
      const state = kept();
      const { value: marks, revision } = state.organization ?? { value: {}, revision: 0 };
      marks[id] = { pinned: false, archived: false, followup: false, ...marks[id], [String(field)]: value };
      state.organization = { value: marks, revision: revision + 1 };
      writeFileSync(`${browserState}.tmp`, JSON.stringify(state));
      renameSync(`${browserState}.tmp`, browserState);
      return state.organization;
    }],
  ];
}
