// What the web cockpit reads of a project, computed on each request from project.json,
// events.jsonl and usage.jsonl: readings may continue in memory; nothing here writes to disk.

import { closeSync, fstatSync, openSync, readSync, statSync } from 'node:fs';
import { basename, join } from 'node:path';
import {
  newestPerRole, SNAPSHOT_SCHEMA_VERSION, type BacklogItem, type Daemon, type DeliveryReceipt, type EventMsg, type OperatorDecisionCard, type ProjectRow,
  type Snapshot, type UsageSummary, type ConversationPage, type ProjectWorkStatus,
} from '@argus/contracts';
import { summarizeUsage } from '@argus/runtime';
import { firstLine, openTasks } from '../flow.js';
import { NAMES } from '../prompts.js';
import { questionId, type FlowEvent, type Project, type Question, type Store } from '../store.js';
import { ROLES, type Backend, type Role, type Step } from '../types.js';

/** What this process knows of a project's run right now. */
export interface Live {
  /** When the run under way started (epoch seconds); null when none is. */
  since: number | null;
  /** Each call under way, by its role and the task it is on: tasks run side by side. */
  calls: Array<{ role: Role; task: string | undefined }>;
  /** Message requests whose Manager turn has not ended. */
  requests: string[];
}

export type Recorded = FlowEvent & { ts: string };

/** Every backend there is, by the name the page shows it by. */
export const BACKEND_LABELS: Record<Backend, string> = { codex: 'Codex', claude: 'Claude Code', copilot: 'Copilot CLI', pi: 'Pi' };
/** A step of a turn, as the page's feed knows it. */
const STEP_KINDS: Record<Step['kind'], string> = { message: 'agent_message', command: 'command_execution', edit: 'file_change' };

const seconds = (iso: string): number => Date.parse(iso) / 1000;

/** A project's name: its own, else its request's first line. */
export const named = ({ name, request }: Project): string => name || request.split('\n')[0]!.slice(0, 80);

/** The result a Manager's letter hands the operator, as the page's result card shows it, named as
 * it was when delivered (a record from before then, by the letter's first line). */
function delivered({ letter, open = [], snapshots, title, ts }: Extract<Recorded, { type: 'handoff' }>, line: number): DeliveryReceipt {
  const targets = open.map(path => ({ path, label: basename(path), source: 'manager', why: '' }));
  return {
    schema_version: 1, delivery_id: `d${line}`, kind: 'task_completed', item_id: '', title: title ?? firstLine(letter),
    summary: letter, status: 'done', review_status: '', delivered_at: seconds(ts), primary_target: targets[0] ?? null, targets,
    ...snapshots ? { snapshots } : {},
  };
}

/** The JSON lines of a file from byte `from` on, and the byte after the last of them, where the
 * next read starts: a line not yet ended waits for it. A line cut short (a full disk, a killed
 * process) is left out rather than failing every read. */
export function jsonLines(path: string, from = 0): { rows: Record<string, unknown>[]; end: number } {
  let bytes: Buffer;
  try {
    const file = openSync(path, 'r');
    try {
      bytes = Buffer.alloc(Math.max(0, fstatSync(file).size - from));
      bytes = bytes.subarray(0, readSync(file, bytes, 0, bytes.length, from));
    } finally { closeSync(file); }
  } catch { return { rows: [], end: from }; }
  const ended = bytes.lastIndexOf('\n') + 1;
  const rows = bytes.toString('utf8', 0, ended).split('\n')
    .flatMap(line => { try { return line ? [JSON.parse(line) as Record<string, unknown>] : []; } catch { return []; } });
  return { rows, end: from + ended };
}

export const readEvents = (store: Store, id: string): Recorded[] =>
  jsonLines(join(store.dir(id), 'events.jsonl')).rows as unknown as Recorded[];

/** Where a fold of the record has got to: the next line, the operator message a Manager letter
 * answers, and the call under way of each role on each task, which tasks run side by side. */
export interface Fold { line: number; asked: number | null; calls: Map<string, string> }

/** The record in the cockpit's event vocabulary, folded on from `fold`. `event_id` is the line,
 * so a replay and the live stream agree; a reply's message id names the operator message it answers. */
export function webEvents(events: Recorded[], fold: Fold = { line: 0, asked: null, calls: new Map() }): EventMsg[] {
  const rows: EventMsg[] = [];
  const { calls } = fold;
  for (const event of events) {
    const line = fold.line++;
    const row = (type: string, fields: Record<string, unknown>): void => {
      rows.push({ type, ts: seconds(event.ts), event_id: line, ...fields });
    };
    const where = 'task' in event && typeof event.task === 'string' ? { item_id: event.task } : {};
    const caller = 'role' in event ? `${event.role}.${where.item_id ?? ''}` : '';
    switch (event.type) {
      case 'message':
        // A message to the Manager waits for its reply; an answer to another role's question does not.
        if (!event.to) fold.asked = line;
        row('ui.operator', { agent_layer: 'operator', text: event.text, message_id: `m${line}-operator` });
        break;
      case 'call.start':
        calls.set(caller, `c${line}`);
        row('provider.request.started', { call_id: `c${line}`, run_label: event.role, agent_layer: event.role, ...where });
        break;
      case 'call.end':
        // A Manager turn that ended without a letter replies to nothing; the next letter answers no message.
        if (event.role === 'manager' && !event.completed) fold.asked = null;
        row('provider.request.completed', {
          call_id: calls.get(caller) ?? '', run_label: event.role, agent_layer: event.role, success: event.completed, error: event.error ?? '', ...where,
        });
        break;
      case 'handoff':
        // Every letter of the Manager's is in the conversation: its reply, and what it hands the team, with whom to.
        if (event.role === 'manager') {
          row('ui.argus', {
            agent_layer: 'manager', to: event.to, text: event.letter, message_id: `m${fold.asked ?? line}-argus`,
            ...event.open ? { delivery: delivered(event, line) } : {},
          });
          fold.asked = null;
        } else if (event.role === 'planner') {
          row('life.planner.verdict', { status: event.to, enqueued_tasks: event.to === 'engineer' ? 1 : 0 });
        } else {
          // A letter between the team's roles reads as what its writer said: a verdict is in its words.
          row('engineer.progress', { kind: 'agent_message', agent_layer: event.role, text: event.letter, ...where });
        }
        break;
      case 'task.added':
        // A Planner's task joins its plan; a task from the Manager starts at once.
        row(event.task.from === 'planner' ? 'life.planner.task_added' : 'life.mission.started',
          { item_id: event.task.id, title: event.task.title, objective: event.task.objective, deps: event.task.after ?? [] });
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
  }
  return rows;
}

/** A reading of the project's record that goes on where it stopped: each call returns the
 * cockpit events of the lines appended since the last, the first call those of all. */
export function follow(store: Store, id: string): () => EventMsg[] {
  const path = join(store.dir(id), 'events.jsonl');
  const fold: Fold = { line: 0, asked: null, calls: new Map() };
  let offset = 0;
  return () => {
    const { rows, end } = jsonLines(path, offset);
    offset = end;
    return webEvents(rows as unknown as Recorded[], fold);
  };
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

/** Whether Resume has work to take up: letters to the Manager no turn took, failed turns, or the
 * open work and the Planner's letters the operator stopped. */
const resumable = (project: Project): boolean => Boolean(project.forManager?.length) || project.status === 'failed'
  || project.status === 'stopped' && (openTasks(project).length > 0 || project.inbox.length > 0);

export function daemon(project: Project, live: Live): Daemon {
  const { since } = live;
  return {
    // Alive until interrupted calls finish recording their ends.
    alive: since !== null, resumable: since === null && resumable(project), pid: since === null ? null : process.pid, uptime_seconds: since === null ? null : Date.now() / 1000 - since,
    started_at_iso: since === null ? null : new Date(since * 1000).toISOString(), backend: project.backend,
    backend_label: BACKEND_LABELS[project.backend],
    read_status: 'ok', read_error: '', protocol_compatible: true, protocol_error: '',
  };
}

/** Each task as the map draws it: after the tasks it waits for, and as it stands. */
function backlog(project: Project): BacklogItem[] {
  const closed = new Set(project.tasks.filter(task => task.status === 'closed').map(task => task.id));
  return project.tasks.map(task => ({
    id: task.id, title: task.title, objective: task.objective, priority: 0, deps: task.after ?? [],
    status: task.status === 'closed' ? 'done' : asked(project, task.id) ? 'blocked'
      : project.failures?.some(failure => failure.task === task.id) ? 'failed'
      : (task.after ?? []).every(id => closed.has(id)) ? 'in_progress' : 'pending',
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

/** One fold owns dialogue rows, task positions and the current operator round's role log.
 * It runs on the whole record at first, then only on newly appended lines. */
export interface Reading {
  end: number;
  fold: Fold;
  rows: Map<string, EventMsg>;
  roleEvents: EventMsg[];
  round: number;
  status: string;
  activityAt: number | null;
  calls: Set<string>;
  tasks: Map<string, { title: string; closed: boolean; line: number; ts: number }>;
  questions: Map<string, EventMsg>;
}

export function projectRecord(events: Recorded[], reading: Reading = {
  end: 0, fold: { line: 0, asked: null, calls: new Map() }, rows: new Map(),
  roleEvents: [], round: 0, status: '', activityAt: null, calls: new Set(), tasks: new Map(), questions: new Map(),
}): Reading {
  for (const event of events) {
    const line = reading.fold.line;
    const ts = seconds(event.ts);
    const converted = webEvents([event], reading.fold);
    const put = (row: EventMsg, id: string, order = line) => reading.rows.set(id, { ...row, row_id: id, order });
    const move = (id: string) => {
      const task = reading.tasks.get(id);
      if (task && !task.closed) Object.assign(task, { line, ts });
    };
    if (event.type === 'message') {
      reading.roleEvents = [];
      reading.round = line;
      reading.activityAt = null;
      const row = converted[0]!;
      const addressed = [...reading.questions.values()].filter(row => row.agent_layer === event.to);
      const question = event.to ? reading.questions.get(event.answer_to ?? '')
        ?? (addressed.length === 1 ? addressed[0] : undefined) : undefined;
      if (question) {
        // The answered pair is new dialogue. Move and version both rows together so
        // the stream sends the question even when it is outside the loaded page.
        put({ ...question, event_id: line }, String(question.row_id));
        put({ ...row, answer_to: question.question_id }, String(row.message_id), line + 0.1);
        reading.questions.delete(String(question.question_id));
        move(String(question.item_id ?? ''));
      } else put(row, String(row.message_id));
    } else if (event.type === 'handoff' && event.to === 'operator' && event.role !== 'manager') {
      const id = questionId({ role: event.role, task: event.task ?? null, question: event.letter });
      const row = { type: 'ui.question', ts, event_id: line, question_id: id, agent_layer: event.role,
        item_id: event.task ?? '', text: event.letter, order: line };
      put(row, `q${line}`);
      reading.questions.set(id, reading.rows.get(`q${line}`)!);
    } else if (event.type === 'handoff' && event.role === 'manager') {
      const row = converted[0]!;
      put(row, String(row.message_id));
      const operatorId = String(row.message_id).replace(/-argus$/, '-operator');
      const operator = reading.rows.get(operatorId);
      if (operator) reading.rows.set(operatorId, { ...operator, answer: event.letter, event_id: line });
    }
    if (event.type === 'task.added') reading.tasks.set(event.task.id, { title: event.task.title, closed: false, line, ts });
    else if (event.type === 'task.closed') {
      move(event.task);
      const task = reading.tasks.get(event.task);
      if (task) task.closed = true;
    } else if (event.type === 'status') {
      if (['stopped', 'failed'].includes(event.status) || ['stopped', 'failed'].includes(reading.status)) {
        for (const id of reading.tasks.keys()) move(id);
      }
      reading.status = event.status;
    } else if ('task' in event && typeof event.task === 'string' && event.type !== 'step' && event.type !== 'call.end') move(event.task);
    else if (event.type === 'call.end' && !event.completed && event.task) move(event.task);

    if (event.type === 'call.end') reading.calls.add(event.callId);
    if (event.type === 'step' || event.type === 'handoff' || event.type === 'call.start' || event.type === 'call.end') {
      // Task lifecycle announcements have no author and never enter a role's log.
      const log = event.type === 'handoff' ? { type: 'engineer.progress', ts, event_id: line,
        agent_layer: event.role, item_id: event.task ?? '', kind: 'agent_message', text: event.letter } : converted[0]!;
      reading.roleEvents = newestPerRole([...reading.roleEvents, log]);
      reading.activityAt = ts;
    }
  }
  return reading;
}

const readings = new Map<string, Reading>();
export function readRecord(store: Store, id: string): Reading {
  const path = join(store.dir(id), 'events.jsonl');
  const previous = readings.get(path);
  const { rows, end } = jsonLines(path, previous?.end ?? 0);
  const reading = projectRecord(rows as unknown as Recorded[], previous);
  reading.end = end;
  readings.set(path, reading);
  return reading;
}

export function conversationRows(reading: Reading, project: Project, live: Live): EventMsg[] {
  const rows = [...reading.rows.values()];
  const items = backlog(project);
  for (const [id, task] of reading.tasks) {
    const item = items.find(item => item.id === id);
    const role = live.calls.find(call => call.task === id)?.role;
    const state = item?.status === 'done' ? 'done' : item?.status === 'blocked' ? 'asking'
      : item?.status === 'failed' ? 'failed' : project.status === 'stopped' ? 'paused'
      : item?.status === 'pending' ? 'queued' : live.since !== null ? 'running' : 'paused';
    rows.push({ type: 'ui.task', row_id: `task-${id}`, item_id: id, title: task.title, state, role,
      ts: task.ts, event_id: task.line, order: task.line + 0.2 });
  }
  return rows.sort((a, b) => Number(a.order) - Number(b.order));
}

export function conversationPage(rows: EventMsg[], cursor: number, limit: number, before = Infinity): ConversationPage {
  const earlier = rows.filter(row => Number(row.order) < before);
  let start = Math.max(0, earlier.length - limit);
  // A question and its answer cross a page boundary together.
  if (start > 0 && earlier[start - 1]?.type === 'ui.question'
    && earlier[start - 1]?.question_id === earlier[start]?.answer_to) start--;
  return { rows: earlier.slice(start), before: start ? Number(earlier[start]!.order) : null, cursor };
}

function projectStatus(project: Project, live: Live, reading: Reading): ProjectWorkStatus {
  const roles = [...new Set(live.calls.map(call => call.role))];
  const task = openTasks(project).length === 1 ? openTasks(project)[0] : undefined;
  const count = openTasks(project).length;
  const running = new Set(live.calls.flatMap(call => call.task ?? [])).size;
  const questions = project.questions.length;
  const stopped = project.status === 'stopped' || project.status === 'failed';
  const foreground = live.requests.length > 0;
  const state = stopped ? 'paused' : foreground || roles.length ? 'running' : questions ? 'paused' : live.since !== null ? 'waiting' : 'idle';
  const label = stopped
    ? { 'zh-CN': `${project.status === 'failed' ? '执行中断' : '已停止'}${count ? `：${count} 个任务暂停` : ''}`, en: `${project.status === 'failed' ? 'Interrupted' : 'Stopped'}${count ? `: ${count} tasks paused` : ''}` }
    : questions ? { 'zh-CN': `等你回答 ${questions} 个问题${running ? ` · ${running} 个任务继续进行` : roles.length ? ' · 团队继续工作' : ''}`, en: `Waiting for ${questions} answer${questions === 1 ? '' : 's'}${roles.length ? ' · Other work continues' : ''}` }
    : foreground ? { 'zh-CN': '正在处理你的请求', en: 'Working on your request' }
    : state === 'running' ? { 'zh-CN': running ? `${running} 个任务进行中` : '团队正在工作', en: running ? `${running} task${running === 1 ? '' : 's'} in progress` : 'The team is working' }
    : state === 'waiting' ? { 'zh-CN': '等待下一步工作', en: 'Waiting for the next step' }
    : { 'zh-CN': project.request ? '已完成' : '等你开始', en: project.request ? 'Done' : 'Waiting for you to start' };
  return { state, reason: stopped ? 'not_running' : questions ? 'operator_input' : state === 'waiting' ? 'awaiting_next_step' : '',
    role: foreground ? 'manager' : roles[0] ?? '', roles, taskId: task?.id ?? '', title: task?.title ?? '', foreground,
    activityAt: reading.activityAt, label };
}

export function snapshot(store: Store, project: Project, live: Live): Snapshot {
  const { id, workdir, backend } = project;
  // What was spent counts every call; what the project runs on, only its own.
  const spent = jsonLines(join(store.dir(id), 'usage.jsonl')).rows;
  const summary = usage(spent);
  const reading = readRecord(store, id);
  const { calls: made } = reading;
  const calls = spent.filter(call => made.has(String(call.callId)));
  // The model each role's last call ran on: with none chosen, the backend's own default.
  const ran = (role?: Role) => calls.filter(call => !role || call.role === role).at(-1);
  const last = ran();
  return {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    session: {
      id, display_name: named(project), objective: project.request, created: seconds(project.created),
      last_active: lastActive(store, id), cwd: workdir, workdir, launch_cwd: workdir,
    },
    daemon: daemon(project, live),
    role_events: reading.roleEvents, role_round: reading.round,
    work_status: projectStatus(project, live, reading),
    roles: ROLES.map(role => {
      const under = live.calls.filter(call => call.role === role);
      return {
        role, backend, backend_label: BACKEND_LABELS[backend], model: project.model ?? String(ran(role)?.model ?? ''), effort: project.effort ?? null, active: under.length > 0,
        label: NAMES[role], status: under.length ? 'running' : 'idle', age_s: null, tasks: under.flatMap(call => call.task ?? []),
      };
    }),
    last_call: last ? {
      backend: String(last.backend), model: String(last.model ?? ''), effort: project.effort ?? '', run_label: String(last.role), role: String(last.role), ts: Number(last.ts),
    } : null,
    backlog: backlog(project),
    recent_events: [],
    spend_usd: summary.cost_usd, spend_status: summary.pricing_status as NonNullable<Snapshot['spend_status']>, usage_summary: summary,
    daemon_commands: null, observability: null, mission_view: null,
    pending_questions: questions(project),
    manager_requests: live.requests.map(request_id => ({ request_id, status: 'running' as const })),
    goal: project.request,
    partial: false,
    diagnostics: [],
  };
}

export function projectRow(store: Store, project: Project, live: Live): ProjectRow {
  const summary = usage(jsonLines(join(store.dir(project.id), 'usage.jsonl')).rows);
  const { since } = live;
  return {
    // A label other than the id is the name the operator gave it.
    id: project.id, label: project.name || project.id, display_name: named(project), objective: project.request,
    cwd: project.workdir, workdir: project.workdir, launch_cwd: project.workdir, last_active: lastActive(store, project.id),
    daemon_alive: since !== null, daemon_pid: since === null ? null : process.pid, uptime_seconds: since === null ? null : Date.now() / 1000 - since,
    active_role: live.calls.at(-1)?.role ?? '', current_task: openTasks(project).map(task => task.title).join('; '),
    unfinished_tasks: openTasks(project).length,
    work_status: projectStatus(project, live, readRecord(store, project.id)),
    spend_usd: summary.cost_usd, known_cost_usd: summary.known_cost_usd,
    spend_status: summary.pricing_status as NonNullable<ProjectRow['spend_status']>, usage_calls: summary.call_count,
  };
}

type MapCard = BacklogItem & { role: string };

/** How far each record has been read for the map, and what the map keeps of it: when each task
 * was added and closed, the latest thing said on it (a message or a letter, not a command), the
 * files its latest letter handed over (once it has closed, its result), the letters written about
 * tasks, by line, and the results the Manager handed the operator, with when its turn began and
 * the tasks closed since the last delivery. */
interface MapReading {
  end: number;
  line: number;
  added: Map<string, number>;
  closed: Map<string, number>;
  actions: Map<string, BacklogItem['latest_action']>;
  opened: Map<string, string[]>;
  letters: { line: number; letter: Record<string, unknown> }[];
  delivered: MapCard[];
  began: number;
  since: string[];
}
const maps = new Map<string, MapReading>();

/** The progress map: a card per task, and on it a step per letter written about it; the letter
 * that opened a task is its objective. A result the Manager hands the operator is a card of its own,
 * done, after the tasks it reports on, named as its delivery is (`d` and its line), its letter the
 * one step on it. A letter's id is its line, and the lines read are the cursor: a page that holds
 * one is sent only the letters after it. Like the live event stream, each reading continues at the
 * last complete line; only map fields are retained in memory. */
export function progressMap(store: Store, project: Project, after: number) {
  const path = join(store.dir(project.id), 'events.jsonl');
  const reading: MapReading = maps.get(path) ?? {
    end: 0, line: 0, added: new Map(), closed: new Map(), actions: new Map(), opened: new Map(), letters: [], delivered: [], began: 0, since: [],
  };
  const { added, closed, actions, opened, letters, delivered, since } = reading;
  const { rows, end } = jsonLines(path, reading.end);
  for (const event of rows as unknown as Recorded[]) {
    const line = reading.line++;
    const ts = seconds(event.ts);
    if (event.type === 'call.start' && event.role === 'manager') reading.began = ts;
    else if (event.type === 'task.added') added.set(event.task.id, ts);
    else if (event.type === 'task.closed') {
      closed.set(event.task, ts);
      since.push(event.task);
    }
    else if (event.type === 'step' && event.task && event.kind === 'message') actions.set(event.task, { role: event.role, text: event.text, ts });
    else if (event.type === 'handoff' && (event.task || event.role === 'manager' && event.to === 'operator' && event.open)) {
      const id = event.task ?? `d${line}`;
      if (event.task) opened.set(event.task, event.open ?? []);
      else {
        // It follows the tasks closed since the last delivery that no other of them waited for.
        const deps = since.filter(task => !since.some(other => project.tasks.find(({ id }) => id === other)?.after?.includes(task)));
        delivered.push({
          id, title: event.title ?? firstLine(event.letter), objective: event.letter, priority: 0, deps, status: 'done', role: 'manager',
          ts: reading.began, started_ts: reading.began, finished_ts: ts, result_paths: event.open!,
        });
        since.length = 0;
      }
      actions.set(id, { role: event.role, text: event.letter, ts });
      letters.push({ line, letter: { id: `e${line}`, item_id: id, type: 'handoff', ts, role: event.role, to: event.to, text: event.letter } });
    }
  }
  reading.end = end;
  maps.set(path, reading);
  const tasks = [...backlog(project).map((item, index) => {
    const question = asked(project, item.id);
    return {
      ...item, ts: added.get(item.id), started_ts: added.get(item.id), finished_ts: closed.get(item.id) ?? null, role: project.tasks[index]!.holder,
      latest_action: actions.get(item.id), result_paths: closed.has(item.id) ? opened.get(item.id) ?? [] : [],
      ...(question ? { pending_question: question.question } : {}),
    };
  }), ...delivered.map(card => ({ ...card, latest_action: actions.get(card.id) }))];
  return { id: `live:${project.id}`, title: project.id, kind: 'live', description: '', read_only: false, tasks,
    events: letters.flatMap(({ line, letter }) => line >= after ? [letter] : []), cursor: String(reading.line) };
}

type CallEnd = Extract<Recorded, { type: 'call.end' }>;

/** One task's calls in order: the role and the letter it ended with.
 * The call whose letter opened the task comes first. */
export function taskTrace(store: Store, project: Project, task: string) {
  const group = (call: CallEnd, ...letters: string[]) => ({
    call_id: call.callId, role: call.role, started_ts: seconds(call.ts) - call.ms / 1000, ended_ts: seconds(call.ts),
    entries: letters.map(text => ({ kind: 'message', text })),
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
