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
import { asker, type FlowEvent, type Project, type Question, type Store } from '../store.js';
import { ROLES, type Backend, type Role, type Step } from '../types.js';

/** What this process knows of a project's run right now. */
export interface Live {
  /** When the run under way started (epoch seconds); null when none is. */
  since: number | null;
  /** Each call under way, by its role and the task it is on: tasks run side by side; a Manager's
   * turn on an operator message names it. */
  calls: Array<{ role: Role; task: string | undefined; answers?: string }>;
  /** Message requests whose Manager turn has not ended. */
  requests: string[];
}

export type Recorded = FlowEvent & { ts: string };

/** Every backend there is, by the name the page shows it by. */
export const BACKEND_LABELS: Record<Backend, string> = { codex: 'Codex', claude: 'Claude Code', copilot: 'Copilot CLI', pi: 'Pi' };
/** A step of a turn, as the page's feed knows it. */
const STEP_KINDS: Record<Step['kind'], string> = { message: 'agent_message', command: 'command_execution', edit: 'file_change' };

const seconds = (iso: string): number => Date.parse(iso) / 1000;
let lastRead = 0;
/** When a snapshot is read: a later read is stamped larger, even within one millisecond. */
const readAt = (): number => (lastRead = Math.max(Date.now(), lastRead + 1));

/** A project's name: its own, else its request's first line. */
export const named = ({ name, request }: Project): string => name || request.split('\n')[0]!;

/** The result a Manager's letter hands the operator, as the page's result card shows it, named as
 * it was when delivered (a record from before then, by the letter's first line). */
function delivered({ letter, open = [], snapshots, title, ts }: Extract<Recorded, { type: 'handoff' }>, line: number, counts: Map<string, number>): DeliveryReceipt {
  const targets = [...new Set(open)].map(path => {
    const delivery_number = (counts.get(path) ?? 0) + 1;
    counts.set(path, delivery_number);
    return { path, label: basename(path), source: 'manager', delivery_number };
  });
  return {
    schema_version: 1, delivery_id: `d${line}`, kind: 'result', item_id: '', title: title ?? firstLine(letter),
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

/** Where a fold of the record has got to: the next line, the operator's messages to the Manager not
 * yet answered (by request, at their line), the one the Manager's turn under way answers, and the
 * call under way of each role on each task, which tasks run side by side, and how often each file
 * has been delivered. A Manager turn and its letter name the message they answer: nothing here is
 * inferred from the order of the record. */
export interface Fold { line: number; asked: Map<string, number>; turn: string | null; calls: Map<string, string>; deliveries: Map<string, number> }

/** The record in the cockpit's event vocabulary, folded on from `fold`. `event_id` is the line,
 * so a replay and the live stream agree; a reply's message id names the operator message it answers. */
export function webEvents(events: Recorded[], fold: Fold = { line: 0, asked: new Map(), turn: null, calls: new Map(), deliveries: new Map() }): EventMsg[] {
  const rows: EventMsg[] = [];
  const { calls } = fold;
  const counts = fold.deliveries;
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
        if (!event.to && event.request_id) fold.asked.set(event.request_id, line);
        row('ui.operator', { agent_layer: 'operator', text: event.text, message_id: `m${line}-operator`, ...(event.request_id ? { request_id: event.request_id } : {}),
          ...(event.attachments ? { attachments: event.attachments } : {}) });
        break;
      case 'call.start':
        if (event.role === 'manager') fold.turn = event.answers ?? null;
        calls.set(caller, `c${line}`);
        row('provider.request.started', { call_id: `c${line}`, run_label: event.role, agent_layer: event.role, ...where });
        break;
      case 'call.end':
        // An interrupted message stays unanswered; Resume takes it up again.
        if (event.role === 'manager') fold.turn = null;
        row('provider.request.completed', {
          call_id: calls.get(caller) ?? '', run_label: event.role, agent_layer: event.role, success: event.completed, error: event.error ?? '', ...where,
        });
        break;
      case 'handoff':
        // Every letter of the Manager's is in the conversation: its reply, and what it hands the team, with whom to.
        if (event.role === 'manager') {
          const at = event.answers === undefined ? undefined : fold.asked.get(event.answers);
          row('ui.argus', {
            agent_layer: 'manager', to: event.to, text: event.letter, message_id: `m${at ?? line}-argus`,
            ...at === undefined ? {} : { request_id: event.answers }, ...event.open ? { delivery: delivered(event, line, counts) } : {},
          });
          if (event.answers) fold.asked.delete(event.answers);
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
  const fold: Fold = { line: 0, asked: new Map(), turn: null, calls: new Map(), deliveries: new Map() };
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

/** The operator's messages a Manager turn is still to answer: each waits for the Manager till its
 * answer is handed on, and one a later message took the place of waits no more. */
const unanswered = (project: Project): Set<string> =>
  new Set((project.forManager ?? []).flatMap(letter => typeof letter === 'string' ? [] : letter.request_id ?? []));

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
      id: asker(question), item_id: task?.id ?? '', revision: 1, status: 'pending', title, reason: '',
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
  activityAt: number | null;
  stateLine: number;
  calls: Set<string>;
  tasks: Map<string, { title: string; line: number; ts: number }>;
  questions: Map<string, EventMsg>;
  /** The results the Manager handed over, by delivery (`d` and its line), each with its files as delivered. */
  delivered: Map<string, Extract<Recorded, { type: 'handoff' }>>;
  map: MapReading;
}

export function projectRecord(events: Recorded[], reading: Reading = {
  end: 0, fold: { line: 0, asked: new Map(), turn: null, calls: new Map(), deliveries: new Map() }, rows: new Map(),
  roleEvents: [], round: 0, activityAt: null, stateLine: 0, calls: new Set(), tasks: new Map(), questions: new Map(), delivered: new Map(),
  map: { added: new Map(), closed: new Map(), actions: new Map(), opened: new Map(), letters: [], qa: new Map(),
    answered: null, briefed: null, worked: new Set(), follows: new Map() },
}): Reading {
  for (const event of events) {
    const line = reading.fold.line;
    const ts = seconds(event.ts);
    const converted = webEvents([event], reading.fold);
    foldMap(reading.map, event, line);
    if (event.type !== 'step') reading.stateLine = line;
    const put = (row: EventMsg, id: string, order = line) => reading.rows.set(id, { ...row, row_id: id, order });
    if (event.type === 'message') {
      reading.roleEvents = [];
      reading.round = line;
      reading.activityAt = null;
      const row = converted[0]!;
      const question = event.to ? reading.questions.get(asker({ role: event.to, task: event.task ?? null })) : undefined;
      if (question) {
        // The answered pair is new dialogue. Move and version both rows together so
        // the stream sends the question even when it is outside the loaded page.
        put({ ...question, event_id: line }, String(question.row_id));
        put({ ...row, answer_to: question.question_id }, String(row.message_id), line + 0.1);
        reading.questions.delete(asker({ role: event.to!, task: event.task ?? null }));
      } else {
        put(row, String(row.message_id));
        if (!event.to && event.request_id) put({ type: 'ui.argus', agent_layer: 'manager', text: '', ts, event_id: line,
          message_id: `m${line}-argus`, request_id: event.request_id }, `m${line}-argus`, line + 0.1);
      }
    } else if (event.type === 'handoff' && event.to === 'operator' && event.role !== 'manager') {
      const row = { type: 'ui.question', ts, event_id: line, question_id: `q${line}`, agent_layer: event.role,
        item_id: event.task ?? '', text: event.letter, order: line };
      put(row, `q${line}`);
      reading.questions.set(asker({ role: event.role, task: event.task ?? null }), reading.rows.get(`q${line}`)!);
    } else if (event.type === 'handoff' && event.role === 'manager') {
      const row = converted[0]!;
      put(row, String(row.message_id));
      const operatorId = String(row.message_id).replace(/-argus$/, '-operator');
      const operator = reading.rows.get(operatorId);
      if (operator) reading.rows.set(operatorId, { ...operator, answer: event.letter, event_id: line });
    }
    if (event.type === 'task.added') reading.tasks.set(event.task.id, { title: event.task.title, line, ts });
    // A task's row is below the last word the conversation shows on it: the operator's answer to it,
    // or the Manager's letter to it.
    else if ((event.type === 'message' || event.type === 'handoff' && event.role === 'manager') && event.task) {
      const task = reading.tasks.get(event.task);
      if (task) task.line = line;
    }

    if (event.type === 'call.end') reading.calls.add(event.callId);
    if (event.type === 'handoff' && event.role === 'manager' && event.open) reading.delivered.set(`d${line}`, event);
    if (event.type === 'step' || event.type === 'handoff' || event.type === 'call.start' || event.type === 'call.end') {
      // Task lifecycle announcements have no author and never enter a role's log.
      if (!(event.type === 'handoff' && event.to === 'operator' && event.role !== 'manager')) {
        const log = event.type === 'handoff' ? { type: 'engineer.progress', ts, event_id: line,
          agent_layer: event.role, item_id: event.task ?? '', kind: 'agent_message', text: event.letter } : converted[0]!;
        reading.roleEvents = newestPerRole([...reading.roleEvents, log]);
      }
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
  const projected = new Map(reading.rows);
  const pending = unanswered(project);
  // Pending replies come from the complete dialogue, including messages interrupted before any words;
  // one sent after a stop waits on the turn before it.
  for (const [request, line] of reading.fold.asked) {
    const operator = reading.rows.get(`m${line}-operator`)!;
    const liveReply = live.calls.some(call => call.answers === request);
    const status = liveReply ? 'running' : !pending.has(request) ? 'replaced' : live.requests.includes(request) ? 'waiting'
      : project.status === 'stopped' ? 'stopped' : project.failures?.some(f => f.role === 'manager') ? 'failed' : 'waiting';
    projected.set(`m${line}-argus`, { type: 'ui.argus', row_id: `m${line}-argus`, message_id: `m${line}-argus`, agent_layer: 'manager',
      request_id: operator.request_id, ts: Number(operator.ts), event_id: reading.stateLine, order: line + 0.1,
      text: '', live: status === 'running' || status === 'waiting', status });
  }
  const rows = [...projected.values()];
  const items = backlog(project);
  const { actions, opened, closed } = reading.map;
  for (const [id, task] of reading.tasks) {
    const item = items.find(item => item.id === id);
    const role = live.calls.find(call => call.task === id)?.role;
    // What was last said on it, unless it waits for an answer, and once it has closed, its result.
    const action = asked(project, id) ? undefined : actions.get(id);
    const state = item?.status === 'done' ? 'done' : item?.status === 'blocked' ? 'asking'
      : item?.status === 'failed' ? 'failed' : project.status === 'stopped' ? 'paused'
      : item?.status === 'pending' ? 'queued' : live.since !== null ? 'running' : 'paused';
    rows.push({ type: 'ui.task', row_id: `task-${id}`, item_id: id, title: task.title, state, role,
      progress: action ? firstLine(action.text) : '', result_paths: closed.has(id) ? opened.get(id) ?? [] : [],
      ts: task.ts, event_id: Math.max(reading.stateLine, actions.get(id)?.line ?? 0), order: task.line + 0.2 });
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

/** The project's one status, and for its snapshot when anything last happened in it: the list reads no record. */
function projectStatus(project: Project, live: Live, activityAt: number | null = null): ProjectWorkStatus {
  const roles = [...new Set(live.calls.map(call => call.role))];
  const task = openTasks(project).length === 1 ? openTasks(project)[0] : undefined;
  const count = openTasks(project).length;
  const running = new Set(live.calls.flatMap(call => call.task ?? [])).size;
  const questions = project.questions.length;
  // Stopped while there is work to resume; a reply to the operator goes on whatever the work does.
  const stopped = project.status === 'failed' || project.status === 'stopped' && resumable(project);
  const foreground = live.requests.length > 0 || live.calls.some(call => call.answers);
  const state = foreground ? 'running' : stopped ? 'paused' : roles.length ? 'running' : questions ? 'paused' : live.since !== null ? 'waiting' : 'idle';
  const label = foreground ? { 'zh-CN': '正在回复你的消息', en: 'Replying to your message' }
    : stopped ? { 'zh-CN': `${project.status === 'failed' ? '执行中断' : '已停止'}${count ? `：${count} 个任务待继续` : ''}`, en: `${project.status === 'failed' ? 'Interrupted' : 'Stopped'}${count ? `: ${count} task${count === 1 ? '' : 's'} to resume` : ''}` }
    : state === 'paused' ? { 'zh-CN': '等你回答', en: 'Waiting for your answer' }
    : state === 'running' ? { 'zh-CN': running ? `${running} 个任务进行中` : '团队正在工作', en: running ? `${running} task${running === 1 ? '' : 's'} in progress` : 'The team is working' }
    : state === 'waiting' ? { 'zh-CN': '等待下一步工作', en: 'Waiting for the next step' }
    : { 'zh-CN': project.request ? '已回复' : '等你开始', en: project.request ? 'Replied' : 'Waiting for you to start' };
  return { state, reason: state === 'paused' && stopped ? 'not_running' : questions ? 'operator_input' : state === 'waiting' ? 'awaiting_next_step' : '',
    role: foreground ? 'manager' : roles[0] ?? '', roles, taskId: task?.id ?? '', title: task?.title ?? '', foreground,
    activityAt, label };
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
    read_at: readAt(),
    session: {
      id, display_name: named(project), objective: project.request, created: seconds(project.created),
      last_active: lastActive(store, id), workdir,
    },
    daemon: daemon(project, live),
    work_status: projectStatus(project, live, reading.activityAt),
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
    spend_usd: summary.cost_usd, spend_status: summary.pricing_status as NonNullable<Snapshot['spend_status']>, usage_summary: summary,
    pending_questions: questions(project),
    manager_requests: [...new Set([...live.requests, ...live.calls.flatMap(call => call.answers ?? [])])]
      .map(request_id => ({ request_id, status: 'running' as const })),
    goal: project.request,
  };
}

export function projectRow(store: Store, project: Project, live: Live): ProjectRow {
  const { since } = live;
  return {
    // A label other than the id is the name the operator gave it.
    id: project.id, label: project.name || project.id, display_name: named(project),
    workdir: project.workdir, last_active: lastActive(store, project.id),
    daemon_alive: since !== null, daemon_pid: since === null ? null : process.pid,
    work_status: projectStatus(project, live),
  };
}

type MapCard = BacklogItem & { role: string; turn_kind?: 'qa' };

/** Map facts in the shared project reading: when each task
 * was added and closed, the latest thing said on it (a message or a letter, not a command), the
 * files its holder's closing letter handed over, the letters written about
 * tasks, by line, the operator's messages to the Manager, and which tasks follow the message whose
 * answer set them going. */
interface MapReading {
  added: Map<string, number>;
  closed: Map<string, number>;
  actions: Map<string, NonNullable<BacklogItem['latest_action']> & { line: number }>;
  opened: Map<string, string[]>;
  letters: { line: number; letter: Record<string, unknown> }[];
  /** Requests to the Manager; initial clarifications share the original request card. */
  qa: Map<string, MapCard>;
  /** The message the Manager's turn under way has answered, whose tasks follow it; and the one
   * whose answer last briefed the Planner, whose tasks follow it. */
  answered: string | null;
  briefed: string | null;
  /** The messages whose answer handed work to the team or handed over files. */
  worked: Set<string>;
  follows: Map<string, string>;
}

/** The progress map: a card per task, and a card for each operator request that starts work or
 * hands over files. Clarifications before the original request starts work share its card,
 * which always stays. Request ids and the Manager's `answers` join the letters to their card,
 * and task handoffs join work to the request that set it going. The line cursor pages letters. */
export function progressMap(store: Store, project: Project, after: number) {
  return mapProjection(readRecord(store, project.id), project, after);
}

function foldMap(reading: MapReading, event: Recorded, line: number): void {
  const { added, closed, actions, opened, letters, qa, worked, follows } = reading;
  const ts = seconds(event.ts);
  if (event.type === 'message' && !event.to && event.request_id) {
    const previous = [...qa.values()].at(-1);
    // Before the original request starts work or delivers files, clarification extends that
    // request. Later exchanges keep their own cards, as do requests queued before any reply.
    if (previous === qa.values().next().value && previous?.status === 'done' && !worked.has(previous.id)) {
      qa.set(event.request_id, previous);
      Object.assign(previous, { status: '', finished_ts: null });
      letters.push({ line, letter: { id: `e${line}`, item_id: previous.id, type: 'turn.asked', ts, role: 'operator', to: 'manager', text: event.text } });
    } else qa.set(event.request_id, {
      id: `q${line}`, turn_kind: 'qa', title: firstLine(event.text), objective: event.text, priority: 0, deps: [], status: '',
      role: 'manager', ts, started_ts: ts, finished_ts: null, result_paths: [],
    });
  }
  else if (event.type === 'message' && event.to && event.task) {
    letters.push({ line, letter: { id: `e${line}`, item_id: event.task, type: 'handoff', ts, role: 'operator', to: event.to, text: event.text } });
  }
  else if (event.type === 'call.start' && event.role === 'manager') reading.answered = null;
  else if (event.type === 'task.added') {
    added.set(event.task.id, ts);
    const follow = event.task.from === 'manager' ? reading.answered : reading.briefed;
    if (follow) follows.set(event.task.id, follow);
  }
  else if (event.type === 'task.closed') closed.set(event.task, ts);
  else if (event.type === 'step' && event.task && event.kind === 'message') actions.set(event.task, { role: event.role, text: event.text, ts, line });
  else if (event.type === 'handoff') {
    if (event.task) {
      if (event.role === 'engineer' || event.role === 'reviewer') {
        if (event.to === 'manager' || event.to === 'planner') opened.set(event.task, event.open ?? []);
        if (event.to !== 'operator') actions.set(event.task, { role: event.role, text: event.letter, ts, line });
      }
      letters.push({ line, letter: { id: `e${line}`, item_id: event.task, type: 'handoff', ts, role: event.role, to: event.to, text: event.letter } });
    }
    if (event.role === 'manager' && event.answers && qa.has(event.answers)) {
      const card = qa.get(event.answers)!;
      // A reply answers the message; work handed to the team answers it once that work is done.
      const replied = event.to === 'operator';
      Object.assign(card, { status: replied ? 'done' : 'in_progress', finished_ts: replied ? ts : null, result_paths: [...new Set([...card.result_paths ?? [], ...event.open ?? []])] });
      // Word for a task under way joins the message to that task.
      if (event.task) card.deps = [event.task];
      reading.answered = card.id;
      if (event.to === 'planner') reading.briefed = card.id;
      if (event.to !== 'operator' || event.open?.length) worked.add(card.id);
      actions.set(card.id, { role: event.role, text: event.letter, ts, line });
      letters.push({ line, letter: { id: `e${line}${event.task ? ':reply' : ''}`, item_id: card.id, type: 'turn.replied', ts, role: event.role, to: event.to, text: event.letter } });
    }
  }
}

function mapProjection(record: Reading, project: Project, after: number) {
  const { added, closed, actions, opened, letters, qa, worked, follows } = record.map;
  // An answered message stays if it is the original request, the latest one, or set work going; one
  // still to be answered while the team answers, and the original request even while it is stopped.
  const answering = project.status !== 'stopped' && project.status !== 'failed';
  const cards = [...new Set(qa.values())];
  const first = cards[0];
  const last = cards.at(-1);
  const waiting = new Set([...unanswered(project)].flatMap(request => qa.get(request) ?? []));
  // Work handed to the team is done once the tasks it went to, or that follow its message, have
  // closed, or with none of its own, once the team has no work going.
  const done = (card: MapCard): number | null => {
    const own = [...card.deps ?? [], ...[...follows].flatMap(([task, follow]) => follow === card.id ? [task] : [])];
    if (own.length ? own.some(task => !closed.has(task)) : project.status !== 'idle') return null;
    return Math.max(card.ts ?? 0, ...own.map(task => closed.get(task)!));
  };
  const action = (id: string) => {
    const found = actions.get(id);
    return found ? { role: found.role, text: found.text, ts: found.ts } : undefined;
  };
  const tasks = [...cards.filter(card => card.status ? card === first || card === last || worked.has(card.id)
    : waiting.has(card) && (answering || card === first)).map(card => {
    const finished = card.status === 'in_progress' ? done(card) : card.finished_ts;
    return { ...card, status: card.status && finished !== null ? 'done' : 'in_progress', finished_ts: finished, latest_action: action(card.id) };
  }), ...backlog(project).map((item, index) => {
    const question = asked(project, item.id);
    const follow = follows.get(item.id);
    return {
      ...item, deps: item.deps?.length || !follow ? item.deps : [follow], ts: added.get(item.id), started_ts: added.get(item.id),
      finished_ts: closed.get(item.id) ?? null, role: project.tasks[index]!.holder,
      latest_action: question ? undefined : action(item.id), result_paths: closed.has(item.id) ? opened.get(item.id) ?? [] : [],
      ...(question ? { pending_question: question.question } : {}),
    };
  })].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
  return { id: `live:${project.id}`, title: project.id, kind: 'live', description: '', read_only: false, tasks,
    events: letters.flatMap(({ line, letter }) => line >= after ? [letter] : []), cursor: String(record.fold.line) };
}

type CallEnd = Extract<Recorded, { type: 'call.end' }>;

/** A task's calls and incoming letters, grouped by the call that wrote them.
 * The call whose letter opened the task comes first. */
export function taskTrace(store: Store, project: Project, task: string) {
  const group = (call: CallEnd) => ({
    call_id: call.callId, role: call.role, started_ts: seconds(call.ts) - call.ms / 1000, ended_ts: seconds(call.ts),
    entries: [] as { kind: string; text: string }[],
  });
  const groups: ReturnType<typeof group>[] = [];
  const ended = new Map<string, CallEnd>();
  const include = (call: CallEnd) => {
    let found = groups.find(group => group.call_id === call.callId);
    if (!found) groups.push(found = group(call));
    return found;
  };
  for (const event of readEvents(store, project.id)) {
    if (event.type === 'call.end') {
      ended.set(`${event.role}.${event.task ?? ''}`, event);
      if (event.task === task) include(event);
    } else if (event.type === 'handoff' && event.task === task) {
      const scope = event.role === 'engineer' || event.role === 'reviewer' ? task : '';
      const call = ended.get(`${event.role}.${scope}`);
      if (call) include(call).entries.push({ kind: 'message', text: event.letter });
    } else if (event.type === 'task.added' && event.task.id === task) {
      const call = ended.get(`${event.task.from}.`);
      if (call) include(call).entries.push({ kind: 'message', text: event.task.objective });
    }
  }
  return { task_id: task, groups };
}
