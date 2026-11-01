// What the operator sets on a project: its directory, its name, the backend, model and reasoning
// effort of all its roles, and a fresh Manager session; and across projects: the sidebar's pins and archive, kept in $ARGUS_HOME/browser-state.json. A project's record is
// changed where it is held: by the work under way on it, or on disk. Its directory and backend
// change only while nothing is under way, since the calls under way work in them.

import { accessSync, constants, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { priceBook } from '../agent/prices.js';
import type { Project, RoleRuntime, Store } from '../store.js';
import { BACKENDS, ROLES, type Backend, type Role, type OfferedModels } from '../types.js';
import { applyRoleRuntimes, roleRuntime } from '../roleRuntime.js';
import { notInstalled } from '../agent/sandbox.js';
import { CATALOG_KIND } from '../agent/models.js';
import { HttpError, type Handler } from './index.js';
import type { Runs } from './runs.js';
import { BACKEND_LABELS } from './views.js';

/** The optional API capabilities these routes give the page. */
export const CAPABILITIES = ['project.workdir.v1', 'project.config.v1', 'project.role-config.v1', 'project.editions.v1'];

/** The settings the page writes: a project has one backend, model and effort, which every role
 * uses. A model or effort is the backend's own word for it; '' or `auto`, the backend's default. */
const SETTINGS: Record<string, (work: Project, value: string) => void> = {
  // A backend session is the backend's own: every role starts afresh on another.
  ARGUS_SKILL_RUNNER_BACKEND: (work, backend) => {
    if (!BACKENDS.includes(backend as Backend)) throw new HttpError(400, `the backend must be one of ${BACKENDS.join(', ')}`, 'invalid_backend');
    if (backend !== work.backend) Object.assign(work, { backend, sessions: {}, model: null, modelAutomatic: true });
  },
  ARGUS_SKILL_MODEL: (work, model) => { work.model = model || null; work.modelAutomatic = !model; },
  ARGUS_SKILL_REASONING_EFFORT: (work, effort) => {
    if (effort) work.effort = effort;
    else delete work.effort;
  },
};

/** The directory a request names, `~` for home, made if it is new; '' when it names none. */
export function directory(asked: string): string {
  const expanded = asked.replace(/^~(?=$|\/)/, homedir());
  if (expanded && !isAbsolute(expanded)) throw new HttpError(400, `${asked} is relative; enter an absolute directory path or a path starting with ~/`, 'workdir_relative');
  const path = expanded && resolve(expanded);
  try {
    // Each missing folder in turn, from the nearest that exists: Node's recursive mkdir never returns
    // for a path under /proc. A root that does not exist (a drive, a share) is its own parent.
    const missing: string[] = [];
    for (let dir = path, below = ''; dir !== below && !existsSync(dir); below = dir, dir = dirname(dir)) missing.unshift(dir);
    if (missing[0] && existsSync(dirname(missing[0])) && !statSync(dirname(missing[0])).isDirectory()) {
      throw new HttpError(400, `${path} has a file as its parent (ENOTDIR).`, 'workdir_not_directory');
    }
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

function roleName(value: unknown = 'manager'): Role {
  if (!ROLES.includes(value as Role)) throw new HttpError(400, 'Unknown agent role.', 'invalid_role');
  return value as Role;
}
function backendName(value: unknown): Backend {
  if (!BACKENDS.includes(value as Backend)) throw new HttpError(400, 'Unknown agent CLI.', 'invalid_backend');
  return value as Backend;
}
function word(value: unknown): string {
  if (value == null || value === '') return '';
  if (typeof value !== 'string' || value.length > 512 || !/^[\w][\w.:/@+[\]-]*$/.test(value)) {
    throw new HttpError(400, 'Enter a model or effort ID, not a command line.', 'invalid_model');
  }
  return /^(auto|default)$/i.test(value) ? '' : value;
}
function route(value: Record<string, unknown>): RoleRuntime {
  const backend = backendName(value.backend);
  const model = word(value.model);
  const effort = word(value.effort);
  if (backend === 'dsh' && model && !/^[^/]+\/.+$/.test(model)) throw new HttpError(400, 'DSH needs a provider/model ID.', 'invalid_model');
  return { backend, model: model || null, modelAutomatic: !model, ...effort ? { effort } : {} };
}

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
      const name = String((await body()).name ?? '').replace(/\s+/g, ' ').trim();
      edit(id!, work => { if (name) work.name = name; else delete work.name; });
      return { ok: true, sid: id, name };
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/config$/, ({ params: [id] }) => {
      const work = project(id!);
      const { backend, model } = work;
      const source = (value: unknown) => value ? 'persisted' : 'default';
      const knob = (name: string, value: string) => ({ name, group: 'Runtime', value, source: source(value), saved: value, default: '', doc: '' });
      return {
        schema_version: 1, generated_at_utc: new Date().toISOString(), revision: work.configRevision ?? 0,
        active_roles: [...new Set(runs.live(work.id).calls.map(call => call.role))],
        roles: ROLES.map(role => {
          const { backend, model, effort, modelAutomatic } = roleRuntime(work, role);
          return { role, backend, backend_label: BACKEND_LABELS[backend], backend_source: 'persisted',
            model: model ?? '', model_automatic: modelAutomatic, model_source: source(model),
            reasoning_effort: effort ?? null, reasoning_effort_source: source(effort), description: '' };
        }),
        operator_knobs: [knob('ARGUS_SKILL_RUNNER_BACKEND', backend), knob('ARGUS_SKILL_MODEL', model ?? '')],
        how_to_change: [], backends: BACKEND_LABELS,
        available_backends: Object.fromEntries(BACKENDS.filter(backend => !notInstalled(backend)).map(backend => [backend, BACKEND_LABELS[backend]])),
      };
    }],
    // A single revision-checked write: either every requested role is saved, or none is.
    ['PUT', /^\/api\/projects\/([^/]+)\/config\/roles$/, async ({ params: [id], body }) => {
      const { roles, revision } = await body();
      if (!Array.isArray(roles) || !roles.length || roles.length > ROLES.length || !Number.isSafeInteger(revision)) {
        throw new HttpError(400, 'Expected roles and the configuration revision.', 'invalid_setting');
      }
      const updates: Partial<Record<Role, RoleRuntime>> = {};
      for (const row of roles) {
        if (!row || typeof row !== 'object') throw new HttpError(400, 'Invalid role configuration.', 'invalid_setting');
        const role = roleName(row.role ?? null);
        if (updates[role]) throw new HttpError(400, 'Duplicate role.', 'invalid_role');
        updates[role] = route(row);
      }
      edit(id!, work => {
        if ((work.configRevision ?? 0) !== revision) throw new HttpError(409, 'Settings changed in another window. Reload before saving.', 'config_conflict');
        applyRoleRuntimes(work, updates);
      });
      return { ok: true, revision: project(id!).configRevision, effective: 'next_turn' };
    }],
    // Explicit, bounded inference only: selecting or listing a model never invokes it.
    ['POST', /^\/api\/projects\/([^/]+)\/model-choices\/probe$/, async ({ params: [id], body }) => {
      const asked = await body();
      const role = roleName(asked.role);
      const work = project(id!);
      const selected = roleRuntime(work, role);
      const { backend, model, effort } = route({ ...selected, ...asked, model: asked.model ?? selected.model });
      const dir = store.dir(work.id);
      const started = Date.now();
      const result = await runs.callAgent({
        backend, role, form: role === 'planner' ? 'plan' : role === 'reviewer' ? 'review' : role, access: 'read', cwd: work.workdir,
        prompt: 'Do not use workspace tools or change files. Finish with the required Argus hand-off form (use hand_off if that tool is provided), addressed to the operator, with the letter containing only: ok.',
        ...model ? { model } : {}, ...effort ? { effort } : {}, signal: AbortSignal.timeout(60_000),
        callsDir: join(dir, 'probes'), usageFile: join(dir, 'usage.jsonl'),
      }).catch((thrown: unknown) => ({ completed: false, error: thrown instanceof Error ? thrown.message : String(thrown) }));
      const accepted = result.completed && 'handoff' in result && result.handoff !== null;
      return { role, backend, model: model ?? '', actual_model: 'model' in result ? result.model : null,
        ok: accepted, error: accepted ? '' : result.error || 'The model replied without the required Argus hand-off form.', duration_ms: Date.now() - started };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/config\/set$/, async ({ params: [id], body }) => {
      const { name, value } = await body();
      const set = SETTINGS[String(name ?? '').trim().toUpperCase()];
      if (!set) throw new HttpError(400, `${String(name)} is not a setting of this project: it has one backend, model and effort, for every role`, 'invalid_setting');
      const given = String(value ?? '').trim();
      const word = /^(auto|default)?$/i.test(given) ? '' : given;
      // Given to the backend's command line as one argument: never an option of its own.
      if (word && !/^\w[\w.:/@+[\]-]*$/.test(word)) throw new HttpError(400, `not a name a backend takes: ${given}`, 'invalid_model');
      edit(id!, work => {
        // Legacy clients explicitly apply a setting to all roles; new clients use config/roles.
        const updates = Object.fromEntries(ROLES.map(role => {
          const draft = { ...work, ...roleRuntime(work, role), sessions: { ...work.sessions } };
          set(draft, word);
          return [role, { backend: draft.backend, model: draft.model, modelAutomatic: draft.modelAutomatic, ...draft.effort ? { effort: draft.effort } : {} }];
        }));
        applyRoleRuntimes(work, updates);
        set(work, word);
      }, set === SETTINGS.ARGUS_SKILL_RUNNER_BACKEND);
      return { name, value: word };
    }],
    // The models to choose from are those the backend names, and the one set if it names it not;
    // the page takes any other by name.
    ['GET', /^\/api\/projects\/([^/]+)\/model-choices$/, async ({ params: [id], url }) => {
      const work = project(id!);
      const role = roleName(url.searchParams.get('role') ?? undefined);
      const selected = roleRuntime(work, role);
      const backend = backendName(url.searchParams.get('backend') ?? selected.backend);
      let error = '';
      // Independent, public metadata only; overlap network work with catalog discovery.
      const prices = priceBook.refresh(url.searchParams.get('refresh') === '1');
      const offered: OfferedModels = await runs.callAgent.models?.(backend, work.workdir, url.searchParams.get('refresh') === '1')
        .catch((cause: unknown) => { error = cause instanceof Error ? cause.message : String(cause); return []; }) ?? [];
      await prices;
      const model = selected.backend === backend ? selected.model : null;
      const named = model && !offered.some(row => row.model === model) ? [...offered, { model, default: false }] : offered;
      return {
        role, backend, installed: !notInstalled(backend), error: error || offered.warning || '',
        catalog_kind: offered.length ? CATALOG_KIND[backend] : 'manual',
        options: named.map(row => {
          // Test/custom catalog implementations share the same strict field projection.
          if (row.price) priceBook.remember(backend, work.workdir, row.model, row.price);
          const pricing = priceBook.quote(backend, work.workdir, row.model);
          return { ...row, price: pricing.rates, pricing };
        }),
      };
    }],
    // The Manager's next turn starts a new session; the record it reads stays as it is.
    ['POST', /^\/api\/projects\/([^/]+)\/reset$/, ({ params: [id] }) => {
      edit(id!, work => { delete work.sessions.manager; });
      return { ok: true };
    }],
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
