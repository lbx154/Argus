// What the web cockpit reads of a project, computed on each request from project.json,
// events.jsonl and usage.jsonl: nothing here is stored and nothing here writes.

import { readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import {
  SNAPSHOT_SCHEMA_VERSION, type BacklogItem, type Daemon, type EventMsg, type OperatorDecisionCard, type ProjectRow,
  type Snapshot, type UsageSummary,
} from '@argus/contracts';
import { summarizeUsage } from '@argus/runtime';
import { nextTask } from '../flow.js';
import type { FlowEvent, Project, Question, Store, TaskStatus } from '../store.js';
import type { Backend, Role } from '../types.js';

/** What this process knows of a project's run right now. */
export interface Live {
  /** When the run under way started (epoch seconds); null when none is. */
  since: number | null;
  /** The role whose call is under way. */
  role: Role | null;
  /** Message requests whose Manager turn has not ended. */
  requests: string[];
}

export type Recorded = FlowEvent & { ts: string };

const ROLES: Role[] = ['manager', 'planner', 'engineer', 'reviewer'];
const NAMES: Record<Role, string> = { manager: 'Manager', planner: 'Planner', engineer: 'Engineer', reviewer: 'Reviewer' };
const BACKENDS: Record<Backend, string> = { codex: 'Codex', claude: 'Claude Code', copilot: 'Copilot CLI', pi: 'Pi' };
const SETTLED: Record<Exclude<TaskStatus, 'open'>, string> = { closed: 'done' };

const seconds = (iso: string): number => Date.parse(iso) / 1000;

function jsonLines(path: string): Record<string, unknown>[] {
  let text: string;
  try { text = readFileSync(path, 'utf8'); } catch { return []; }
  return text.split('\n').filter(Boolean).map(line => JSON.parse(line) as Record<string, unknown>);
}

export const readEvents = (store: Store, id: string): Recorded[] =>
  jsonLines(join(store.dir(id), 'events.jsonl')) as unknown as Recorded[];

/** The record in the cockpit's event vocabulary. `event_id` is the line, so a replay and the
 * live stream agree; a reply's message id names the operator message it answers. */
export function webEvents(events: Recorded[]): EventMsg[] {
  const rows: EventMsg[] = [];
  let asked: number | null = null;
  let call = '';
  events.forEach((event, line) => {
    const row = (type: string, fields: Record<string, unknown>): void => {
      rows.push({ type, ts: seconds(event.ts), event_id: line, ...fields });
    };
    const where = 'task' in event && typeof event.task === 'string' ? { item_id: event.task } : {};
    switch (event.type) {
      case 'message':
        asked = line;
        row('ui.operator', { agent_layer: 'operator', text: event.text, message_id: `m${line}-operator` });
        break;
      case 'call.start':
        call = `c${line}`;
        row('provider.request.started', { call_id: call, run_label: event.role, agent_layer: event.role, ...where });
        break;
      case 'call.end':
        row('provider.request.completed', {
          call_id: call, run_label: event.role, agent_layer: event.role, success: event.completed, error: event.error ?? '', ...where,
        });
        break;
      case 'handoff':
        // The Manager's letter is its reply, unless it asks: the decision card carries a question.
        if (event.role === 'manager' && event.to !== 'operator') {
          row('ui.argus', { agent_layer: 'manager', text: event.letter, message_id: `m${asked ?? line}-argus` });
          asked = null;
        } else if (event.role === 'planner') {
          row('life.planner.verdict', { status: event.to, enqueued_tasks: event.to === 'engineer' ? 1 : 0 });
        } else if (event.role === 'engineer' && event.to === 'reviewer') {
          row('engineer.progress', { kind: 'agent_message', agent_layer: 'engineer', text: event.letter, ...where });
        } else if (event.role !== 'manager') {
          row('round.review.completed', {
            status: event.to, reason: event.letter, review_source: event.role === 'reviewer' ? 'reviewer' : 'engineer_handoff', ...where,
          });
        }
        break;
      case 'task.added':
        // A Planner's task joins its plan; a task from the Manager starts at once.
        row(event.task.from === 'planner' ? 'life.planner.task_added' : 'life.mission.started',
          { item_id: event.task.id, title: event.task.title, objective: event.task.objective, deps: [] });
        break;
      case 'task.closed':
        row('life.mission.completed', { item_id: event.task, status: 'done', success: true });
        break;
      case 'status':
        if (event.status === 'failed') row('life.status', { operator_alert: true, text: event.error ?? 'The work stopped.' });
        break;
    }
  });
  return rows;
}

/** The conversation, as the transcript endpoint returns it. */
export const turns = (events: EventMsg[]) => events.filter(event => event.type === 'ui.operator' || event.type === 'ui.argus')
  .map(event => ({ ts: event.ts, role: event.type === 'ui.operator' ? 'operator' : 'argus', text: event.text, message_id: event.message_id }));

/** A question's id names who asked it and its words, so a new question is a new card. */
export function questionId({ role, task, question }: Question): string {
  let hash = 0x811c9dc5;
  for (const char of `${role}\n${task}\n${question}`) hash = Math.imul(hash ^ char.codePointAt(0)!, 0x01000193) >>> 0;
  return `q-${hash.toString(16)}`;
}

function usage(store: Store, id: string): UsageSummary {
  return summarizeUsage(jsonLines(join(store.dir(id), 'usage.jsonl')).map(row => {
    const tokens = (row.tokens ?? {}) as Record<string, number | undefined>;
    const cost = typeof row.cost_usd === 'number' ? row.cost_usd : null;
    return {
      input_tokens: tokens.input_tokens ?? null, cached_input_tokens: tokens.cached_input_tokens ?? null, cache_write_tokens: null,
      output_tokens: tokens.output_tokens ?? null, reasoning_output_tokens: tokens.reasoning_output_tokens ?? null, total_nano_aiu: null,
      cost_usd: cost, pricing_status: cost === null ? 'unpriced' : 'priced', premium_requests: null, premium_request_cost_usd: null, model_usage: [],
    };
  }));
}

export function daemon(project: Project, live: Live): Daemon {
  const { since } = live;
  return {
    alive: since !== null, pid: since === null ? null : process.pid, uptime_seconds: since === null ? null : Date.now() / 1000 - since,
    started_at_iso: since === null ? null : new Date(since * 1000).toISOString(), backend: project.backend,
    backend_label: BACKENDS[project.backend], control_available: true,
    read_status: 'ok', read_error: '', protocol_compatible: true, protocol_error: '',
  };
}

function backlog(project: Project): BacklogItem[] {
  const next = nextTask(project);
  return project.tasks.map(task => ({
    id: task.id, title: task.title, objective: task.objective, priority: 0, deps: [],
    status: task.status !== 'open' ? SETTLED[task.status] : project.question?.task === task.id ? 'blocked' : task === next ? 'in_progress' : 'pending',
  }));
}

/** The open question as the cockpit's decision card, answered in the operator's own words. */
function questions(project: Project): Record<string, unknown>[] {
  const { question } = project;
  if (!question) return [];
  const task = project.tasks.find(task => task.id === question.task);
  const title = task?.title ?? `The ${NAMES[question.role]} asks`;
  const card: OperatorDecisionCard = {
    id: questionId(question), item_id: task?.id ?? '', revision: 1, status: 'pending', title, reason: '',
    question: question.question, evidence: [], options_source: 'agent', selected_option: '', note: '',
    options: [],
  };
  return [{ id: task?.id ?? question.role, title, status: 'blocked', operator_decision: card }];
}

const lastActive = (store: Store, id: string): number => statSync(join(store.dir(id), 'project.json')).mtimeMs / 1000;

export function snapshot(store: Store, project: Project, live: Live): Snapshot {
  const summary = usage(store, project.id);
  const { id, workdir, backend } = project;
  return {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    session: {
      id, display_name: project.request.split('\n')[0]!.slice(0, 80), objective: project.request, created: seconds(project.created),
      last_active: lastActive(store, id), cwd: workdir, workdir, launch_cwd: workdir,
    },
    daemon: daemon(project, live),
    roles: ROLES.map(role => ({
      role, backend, backend_label: BACKENDS[backend], model: project.model ?? '', effort: null, active: live.role === role,
      label: NAMES[role], status: live.role === role ? 'running' : 'idle', age_s: null,
    })),
    last_call: null,
    backlog: backlog(project),
    recent_events: [],
    spend_usd: summary.cost_usd, spend_status: summary.pricing_status as NonNullable<Snapshot['spend_status']>, usage_summary: summary,
    daemon_commands: null, observability: null, mission_view: null,
    continuous: { enabled: false, objective: '' },
    pending_questions: questions(project),
    manager_requests: live.requests.map(request_id => ({ request_id, status: 'running' as const })),
    launch_hold: null,
    goal: project.request,
    partial: false,
    diagnostics: [],
  };
}

export function projectRow(store: Store, project: Project, live: Live): ProjectRow {
  const summary = usage(store, project.id);
  const { since } = live;
  return {
    // The page names an unnamed project by its objective; a label other than the id would be a name given to it.
    id: project.id, label: project.id, display_name: '', objective: project.request,
    cwd: project.workdir, workdir: project.workdir, launch_cwd: project.workdir, last_active: lastActive(store, project.id),
    daemon_alive: since !== null, daemon_pid: since === null ? null : process.pid, uptime_seconds: since === null ? null : Date.now() / 1000 - since,
    active_role: live.role ?? '', current_task: nextTask(project)?.title ?? '',
    unfinished_tasks: project.tasks.filter(task => task.status === 'open').length,
    spend_usd: summary.cost_usd, known_cost_usd: summary.known_cost_usd,
    spend_status: summary.pricing_status as NonNullable<ProjectRow['spend_status']>, usage_calls: summary.call_count,
  };
}
