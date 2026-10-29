// What the web cockpit reads of a project, computed on each request from project.json,
// events.jsonl and usage.jsonl: nothing here is stored and nothing here writes.

import { readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import {
  SNAPSHOT_SCHEMA_VERSION, type BacklogItem, type Daemon, type EventMsg, type OperatorDecisionCard, type ProjectRow,
  type Snapshot, type UsageSummary,
} from '@argus/contracts';
import { summarizeUsage } from '@argus/runtime';
import { openTasks } from '../flow.js';
import { NAMES } from '../prompts.js';
import type { FlowEvent, Project, Question, Store } from '../store.js';
import { ROLES, type Backend, type Role, type Step } from '../types.js';

/** What this process knows of a project's run right now. */
export interface Live {
  /** When the run under way started (epoch seconds); null when none is. */
  since: number | null;
  /** The role of each call under way: tasks run side by side. */
  roles: Role[];
  /** Paused: the calls under way end, and no other starts. */
  draining: boolean;
  /** Message requests whose Manager turn has not ended. */
  requests: string[];
}

export type Recorded = FlowEvent & { ts: string };

const BACKENDS: Record<Backend, string> = { codex: 'Codex', claude: 'Claude Code', copilot: 'Copilot CLI', pi: 'Pi' };
/** A step of a turn, as the page's feed knows it. */
const STEP_KINDS: Record<Step['kind'], string> = { message: 'agent_message', command: 'command_execution', edit: 'file_change' };

const seconds = (iso: string): number => Date.parse(iso) / 1000;

function jsonLines(path: string): Record<string, unknown>[] {
  let text: string;
  try { text = readFileSync(path, 'utf8'); } catch { return []; }
  // A line cut short (a full disk, a killed process) is left out rather than failing every read.
  return text.split('\n').flatMap(line => { try { return line ? [JSON.parse(line) as Record<string, unknown>] : []; } catch { return []; } });
}

export const readEvents = (store: Store, id: string): Recorded[] =>
  jsonLines(join(store.dir(id), 'events.jsonl')) as unknown as Recorded[];

/** The record in the cockpit's event vocabulary. `event_id` is the line, so a replay and the
 * live stream agree; a reply's message id names the operator message it answers. */
export function webEvents(events: Recorded[]): EventMsg[] {
  const rows: EventMsg[] = [];
  let asked: number | null = null;
  /** The call under way of each role on each task, which tasks run side by side. */
  const calls = new Map<string, string>();
  events.forEach((event, line) => {
    const row = (type: string, fields: Record<string, unknown>): void => {
      rows.push({ type, ts: seconds(event.ts), event_id: line, ...fields });
    };
    const where = 'task' in event && typeof event.task === 'string' ? { item_id: event.task } : {};
    const caller = 'role' in event ? `${event.role}.${where.item_id ?? ''}` : '';
    switch (event.type) {
      case 'message':
        // A message to the Manager waits for its reply; an answer to another role's question does not.
        if (!event.to) asked = line;
        row('ui.operator', { agent_layer: 'operator', text: event.text, message_id: `m${line}-operator` });
        break;
      case 'call.start':
        calls.set(caller, `c${line}`);
        row('provider.request.started', { call_id: `c${line}`, run_label: event.role, agent_layer: event.role, ...where });
        break;
      case 'call.end':
        // A Manager turn that ended without a letter replies to nothing; the next letter answers no message.
        if (event.role === 'manager' && !event.completed) asked = null;
        row('provider.request.completed', {
          call_id: calls.get(caller) ?? '', run_label: event.role, agent_layer: event.role, success: event.completed, error: event.error ?? '', ...where,
        });
        break;
      case 'handoff':
        // Every letter of the Manager's is in the conversation: its reply, and what it hands the team.
        if (event.role === 'manager') {
          row('ui.argus', { agent_layer: 'manager', text: event.letter, message_id: `m${asked ?? line}-argus` });
          asked = null;
        } else if (event.role === 'planner') {
          row('life.planner.verdict', { status: event.to, enqueued_tasks: event.to === 'engineer' ? 1 : 0 });
        } else if (event.role === 'engineer' && event.to === 'reviewer') {
          row('engineer.progress', { kind: 'agent_message', agent_layer: 'engineer', text: event.letter, ...where });
        } else {
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
      case 'step':
        row('engineer.progress', { kind: STEP_KINDS[event.kind], agent_layer: event.role, text: event.text, ...where });
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

function usage(rows: Record<string, unknown>[]): UsageSummary {
  return summarizeUsage(rows.map(row => {
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
    alive: since !== null, draining: live.draining, pid: since === null ? null : process.pid, uptime_seconds: since === null ? null : Date.now() / 1000 - since,
    started_at_iso: since === null ? null : new Date(since * 1000).toISOString(), backend: project.backend,
    backend_label: BACKENDS[project.backend], control_available: true,
    read_status: 'ok', read_error: '', protocol_compatible: true, protocol_error: '',
  };
}

function backlog(project: Project): BacklogItem[] {
  return project.tasks.map(task => ({
    id: task.id, title: task.title, objective: task.objective, priority: 0, deps: [],
    status: task.status === 'closed' ? 'done' : asked(project, task.id) ? 'blocked' : 'in_progress',
  }));
}

/** Each open question as a decision card of the cockpit, answered in the operator's own words. */
function questions(project: Project): Record<string, unknown>[] {
  return project.questions.map(question => {
    const task = project.tasks.find(task => task.id === question.task);
    const title = task?.title ?? `The ${NAMES[question.role]} asks`;
    const card: OperatorDecisionCard = {
      id: questionId(question), item_id: task?.id ?? '', revision: 1, status: 'pending', title, reason: '',
      question: question.question, evidence: [], options_source: 'agent', selected_option: '', note: '',
      options: [],
    };
    return { id: task?.id ?? question.role, title, status: 'blocked', operator_decision: card };
  });
}

const asked = (project: Project, task: string): Question | undefined => project.questions.find(question => question.task === task);

const lastActive = (store: Store, id: string): number => statSync(join(store.dir(id), 'project.json')).mtimeMs / 1000;

export function snapshot(store: Store, project: Project, live: Live): Snapshot {
  const { id, workdir, backend } = project;
  const calls = jsonLines(join(store.dir(id), 'usage.jsonl'));
  const summary = usage(calls);
  // The model each role's last call ran on: with none chosen, the backend's own default.
  const ran = (role?: Role) => calls.filter(call => !role || call.role === role).at(-1);
  const last = ran();
  return {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    session: {
      id, display_name: project.name || project.request.split('\n')[0]!.slice(0, 80), objective: project.request, created: seconds(project.created),
      last_active: lastActive(store, id), cwd: workdir, workdir, launch_cwd: workdir,
    },
    daemon: daemon(project, live),
    roles: ROLES.map(role => ({
      role, backend, backend_label: BACKENDS[backend], model: project.model ?? String(ran(role)?.model ?? ''), effort: project.effort ?? null, active: live.roles.includes(role),
      label: NAMES[role], status: live.roles.includes(role) ? 'running' : 'idle', age_s: null,
    })),
    last_call: last ? {
      backend: String(last.backend), model: String(last.model ?? ''), effort: project.effort ?? '', run_label: String(last.role), role: String(last.role), ts: Number(last.ts),
    } : null,
    // Letters left for the Manager when the work stopped are work Resume takes up, as a task cut off is.
    backlog: [...backlog(project), ...live.since === null ? (project.forManager ?? []).map((letter, index) => ({
      id: `manager-${index + 1}`, title: letter.split('\n')[0]!.slice(0, 100), objective: letter, priority: 0, deps: [], status: 'in_progress' as const,
    })) : []],
    recent_events: [],
    spend_usd: summary.cost_usd, spend_status: summary.pricing_status as NonNullable<Snapshot['spend_status']>, usage_summary: summary,
    daemon_commands: null, observability: null, mission_view: null,
    pending_questions: questions(project),
    manager_requests: live.requests.map(request_id => ({ request_id, status: 'running' as const })),
    launch_hold: null,
    goal: project.request,
    partial: false,
    diagnostics: [],
  };
}

export function projectRow(store: Store, project: Project, live: Live): ProjectRow {
  const summary = usage(jsonLines(join(store.dir(project.id), 'usage.jsonl')));
  const { since } = live;
  return {
    // The page names an unnamed project by its objective; a label other than the id is the name the operator gave it.
    id: project.id, label: project.name || project.id, display_name: project.name ?? '', objective: project.request,
    cwd: project.workdir, workdir: project.workdir, launch_cwd: project.workdir, last_active: lastActive(store, project.id),
    daemon_alive: since !== null, daemon_pid: since === null ? null : process.pid, uptime_seconds: since === null ? null : Date.now() / 1000 - since,
    active_role: live.roles.at(-1) ?? '', current_task: openTasks(project).map(task => task.title).join('; '),
    unfinished_tasks: openTasks(project).length,
    spend_usd: summary.cost_usd, known_cost_usd: summary.known_cost_usd,
    spend_status: summary.pricing_status as NonNullable<ProjectRow['spend_status']>, usage_calls: summary.call_count,
  };
}

/** The progress map: a card per task, and on it a step per letter written about it; the letter
 * that opened a task is its objective. A letter's id is its line, and the lines read are the
 * cursor: a page that holds one is sent only the letters after it. */
export function progressMap(store: Store, project: Project, after: number) {
  const added = new Map<string, number>();
  const closed = new Map<string, number>();
  const letters: Record<string, unknown>[] = [];
  const events = readEvents(store, project.id);
  events.forEach((event, line) => {
    if (event.type === 'task.added') added.set(event.task.id, seconds(event.ts));
    else if (event.type === 'task.closed') closed.set(event.task, seconds(event.ts));
    else if (event.type === 'handoff' && event.task && line >= after) {
      letters.push({ id: `e${line}`, item_id: event.task, type: 'handoff', ts: seconds(event.ts), role: event.role, to: event.to, text: event.letter });
    }
  });
  const tasks = backlog(project).map((item, index) => {
    const question = asked(project, item.id);
    return {
      ...item, ts: added.get(item.id), started_ts: added.get(item.id), finished_ts: closed.get(item.id) ?? null, role: project.tasks[index]!.holder,
      ...(question ? { pending_question: question.question } : {}),
    };
  });
  const cursor = String(events.length);
  return { id: `live:${project.id}`, title: project.id, kind: 'live', description: '', read_only: false, tasks, events: letters, cursor, history_cursor: cursor };
}

/** What the page asks before it loads the map: the whole map is one answer, with no part to choose. */
export function mapInfo(store: Store, project: Project) {
  return {
    task_count: project.tasks.length, event_bytes: statSync(join(store.dir(project.id), 'events.jsonl'), { throwIfNoEntry: false })?.size ?? 0,
    requires_choice: false, current_task_id: null, current_task_ts: 0, current_event_ts: 0,
  };
}

type CallEnd = Extract<Recorded, { type: 'call.end' }>;

/** One task's calls in order: the role, the letter it ended with, and where its raw output is.
 * The call whose letter opened the task comes first. */
export function taskTrace(store: Store, project: Project, task: string) {
  const group = (call: CallEnd, ...letters: string[]) => ({
    call_id: call.callId, role: call.role, started_ts: seconds(call.ts) - call.ms / 1000, ended_ts: seconds(call.ts),
    output: join(store.dir(project.id), 'calls', `${call.callId}.jsonl`), entries: letters.map(text => ({ kind: 'message', text })),
  });
  const groups: ReturnType<typeof group>[] = [];
  let last: CallEnd | undefined;
  for (const event of readEvents(store, project.id)) {
    if (event.type === 'call.end') last = event;
    if (event.type === 'call.end' && event.task === task) groups.push(group(event));
    else if (event.type === 'handoff' && event.task === task) groups.at(-1)!.entries.push({ kind: 'message', text: event.letter });
    else if (event.type === 'task.added' && event.task.id === task && last) groups.push(group(last, event.task.objective));
  }
  return { task_id: task, groups };
}
