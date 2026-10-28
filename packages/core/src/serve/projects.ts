// What the operator sets on a project: its directory, and the backend, model and reasoning effort
// of all its roles. A project's record is changed only while nothing is under way on it, since the
// work under way holds the record and writes it back.

import { statSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve } from 'node:path';
import type { Project, Store } from '../store.js';
import { BACKENDS, ROLES, type Backend } from '../types.js';
import { HttpError, type Handler } from './index.js';
import type { Runs } from './runs.js';
import { daemon } from './views.js';

/** The optional API capabilities these routes give the page. */
export const CAPABILITIES = ['project.workdir.v1'];

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
