// What the web cockpit reads of a project, computed on each request from project.json,
// events.jsonl and usage.jsonl: readings may continue in memory; nothing here writes to disk.

import { closeSync, fstatSync, openSync, readSync, statSync } from 'node:fs';
import { basename, join } from 'node:path';
import {
  newestPerRole, SNAPSHOT_SCHEMA_VERSION, type BacklogItem, type BacklogSummary, type Daemon, type DeliveryReceipt, type EventMsg, type OperatorDecisionCard, type ProjectRow,
  type Snapshot, type ProjectUsageSummary, type UsageSummaryRecord, type ConversationPage, type ProjectWorkStatus,
} from '@argus/contracts';
import { summarizeUsage } from '@argus/runtime';
import { firstLine, openTasks } from '../flow.js';
import { NAMES } from '../prompts.js';
import { ManagerInputs } from '../inputs.js';
import { asker, type FlowEvent, type Project, type Question, type Store } from '../store.js';
import { ROLES, type Backend, type Role, type Step } from '../types.js';

/** What this process knows of a project's run right now. */
export interface Live {
  /** When the run under way started (epoch seconds); null when none is. */
  since: number | null;
  /** Each call under way, by its role and the task it is on: tasks run side by side; a Manager's
   * turn on an operator message names it. */
  calls: Array<{ role: Role; task: string | undefined; started_ts: number; answers?: string; words?: string; wordsKind?: 'process' | 'answer' }>;
  /** Message requests whose Manager turn has not ended. */
  requests: string[];
}

export type Recorded = FlowEvent & { ts: string };

/** Every backend there is, by the name the page shows it by. */
export const BACKEND_LABELS: Record<Backend, string> = { codex: 'Codex', claude: 'Claude Code', copilot: 'Copilot CLI', pi: 'Pi' };
/** A step of a turn, as the page's feed knows it. */
const STEP_KINDS: Record<Step['kind'], string> = { message: 'agent_message', command: 'command_execution', edit: 'file_change', thought: 'reasoning' };

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
 * yet answered (by request, at their line), the call under way of each role on each task, and
 * how often each file has been delivered. Calls bind to operator requests or incoming team
 * letters; Planner letters and tasks carry the originating reply through later turns. */
type FoldCall = { id: string; reply: string; order: number; handed: boolean; answers?: string; plannerTaken?: number; source?: string };
export interface Fold {
  line: number;
  asked: Map<string, number>;
  // Calls retain their reply until an accepted handoff is recorded.
  calls: Map<string, FoldCall>;
  /** Team input identity, including pending legacy form corrections. */
  manager: ManagerInputs;
  deliveries: Map<string, number>;
  plannerInbox: Array<string | undefined>;
  taskReplies: Map<string, string>;
}
const newFold = (): Fold => ({ line: 0, asked: new Map(), calls: new Map(), manager: new ManagerInputs(), deliveries: new Map(),
  plannerInbox: [], taskReplies: new Map() });

/** The record in the cockpit's event vocabulary, folded on from `fold`. `event_id` is the line,
 * so a replay and the live stream agree; a reply's message id names the operator message it answers. */
export function webEvents(events: Recorded[], fold: Fold = newFold()): EventMsg[] {
  const rows: EventMsg[] = [];
  const { calls } = fold;
  const counts = fold.deliveries;
  for (const event of events) {
    const line = fold.line++;
    const input = fold.manager.take(event, line);
    const row = (type: string, fields: Record<string, unknown>): void => {
      rows.push({ type, ts: seconds(event.ts), event_id: line, ...fields });
    };
    const where = 'task' in event && typeof event.task === 'string' ? { item_id: event.task } : {};
    const caller = 'role' in event ? `${event.role}.${event.type === 'handoff' && (event.role === 'manager' || event.role === 'planner') ? '' : where.item_id ?? ''}` : '';
    switch (event.type) {
      case 'message':
        // A message to the Manager waits for its reply; an answer to another role's question does not.
        if (!event.to && event.request_id) fold.asked.set(event.request_id, line);
        if (event.to === 'planner') {
          const reply = event.task ? fold.taskReplies.get(event.task) : calls.get('planner.')?.reply;
          fold.plannerInbox.push(reply);
        }
        row('ui.operator', { agent_layer: 'operator', text: event.text, message_id: `m${line}-operator`, ...(event.request_id ? { request_id: event.request_id } : {}),
          ...(event.attachments ? { attachments: event.attachments } : {}) });
        break;
      case 'call.start': {
        const previous = calls.get(caller);
        const at = event.answers ? fold.asked.get(event.answers) : undefined;
        // Flow reads the inbox prefix as the call starts, and consumes it only after an
        // accepted handoff. A correction reads that same prefix; a resumed turn can read
        // newly arrived letters too. Old records retain these same call/letter boundaries.
        const plannerTaken = event.role === 'planner' ? event.planner_inputs ?? fold.plannerInbox.length : undefined;
        const inputs = event.role === 'planner' ? fold.plannerInbox.slice(0, plannerTaken) : [];
        const sources = new Set(inputs);
        const source = event.role === 'planner' && sources.size === 1 && plannerTaken === inputs.length
          ? sources.values().next().value : undefined;
        // Mixed or unknown input has no single operator owner. Keep its own row and let
        // new tasks inherit it, rather than choosing either request by arrival order.
        const continuing = event.role === 'planner' && previous && !previous.handed && previous.source === source ? previous : undefined;
        calls.set(caller, { id: `c${line}`, reply: input ? `m${input.callLine}-argus` : continuing?.reply ?? source ?? `m${at ?? line}-argus`,
          order: input ? input.line + 0.1 : continuing?.order ?? (at ?? line) + 0.1,
          handed: false, ...plannerTaken !== undefined ? { plannerTaken, ...source ? { source } : {} } : {}, ...event.answers ? { answers: event.answers } : {} });
        row('provider.request.started', { call_id: `c${line}`, run_label: event.role, agent_layer: event.role, ...where });
        break;
      }
      case 'call.end':
        // An interrupted message stays unanswered; Resume takes it up again.
        row('provider.request.completed', {
          call_id: calls.get(caller)?.id ?? '', run_label: event.role, agent_layer: event.role, success: event.completed, error: event.error ?? '', ...where,
        });
        if (event.role !== 'manager' && event.role !== 'planner') calls.delete(caller);
        break;
      case 'handoff':
        if (event.role === 'planner' && !calls.get(caller)?.handed) fold.plannerInbox.splice(0, calls.get(caller)?.plannerTaken ?? 0);
        if (calls.has(caller)) calls.get(caller)!.handed = true;
        // Every letter of the Manager's is in the conversation: its reply, and what it hands the team, with whom to.
        if (event.role === 'manager') {
          const at = event.answers === undefined ? undefined : fold.asked.get(event.answers);
          row('ui.argus', {
            agent_layer: 'manager', to: event.to, text: event.letter, message_id: at === undefined ? calls.get(caller)?.reply ?? `m${line}-argus` : `m${at}-argus`,
            ...at === undefined ? {} : { request_id: event.answers }, ...event.open ? { delivery: delivered(event, line, counts) } : {},
          });
          if (event.to === 'planner') fold.plannerInbox.push(String(rows.at(-1)!.message_id));
          if (event.answers) fold.asked.delete(event.answers);
        } else if (event.role === 'planner') {
          row('life.planner.verdict', { status: event.to, enqueued_tasks: event.to === 'engineer' ? 1 : 0 });
        } else {
          // A letter between the team's roles reads as what its writer said: a verdict is in its words.
          row('engineer.progress', { kind: 'agent_message', agent_layer: event.role, text: event.letter, ...where });
        }
        if (event.task && (event.role === 'manager' || event.role === 'planner')) {
          const reply = calls.get(caller)?.reply;
          if (reply && (event.role === 'manager' || !fold.taskReplies.has(event.task))) fold.taskReplies.set(event.task, reply);
        }
        if (event.task && event.to === 'planner' && event.role !== 'manager' && event.role !== 'planner') {
          const reply = fold.taskReplies.get(event.task);
          fold.plannerInbox.push(reply);
        }
        break;
      case 'task.added':
        {
          const reply = calls.get(`${event.task.from}.`)?.reply;
          if (reply) fold.taskReplies.set(event.task.id, reply);
        }
        // A Planner's task joins its plan; a task from the Manager starts at once.
        row(event.task.from === 'planner' ? 'life.planner.task_added' : 'life.mission.started',
          { item_id: event.task.id, title: event.task.title, objective: event.task.objective, deps: event.task.after ?? [] });
        break;
      case 'task.closed':
        row('life.mission.completed', { item_id: event.task, status: 'done', success: true });
        break;
      case 'step': {
        const request = event.role === 'manager' && event.kind === 'message' ? calls.get(caller)?.answers : undefined;
        row('engineer.progress', { kind: STEP_KINDS[event.kind], agent_layer: event.role, text: event.text, ...where,
          ...request ? { request_id: request } : {} });
        break;
      }
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
  const fold: Fold = newFold();
  let offset = 0;
  return () => {
    const { rows, end } = jsonLines(path, offset);
    offset = end;
    return webEvents(rows as unknown as Recorded[], fold);
  };
}

function usage(rows: Record<string, unknown>[], calls: Live['calls'] = [], now = 0): ProjectUsageSummary {
  const grouped = { by_role: new Map<string | null, UsageSummaryRecord[]>(), by_model: new Map<string | null, UsageSummaryRecord[]>(), by_date: new Map<string | null, UsageSummaryRecord[]>() };
  const intervals: Array<[number, number]> = [];
  let missing = 0;
  const records = rows.map(row => {
    const tokens = (row.tokens ?? {}) as Record<string, number | undefined>;
    const cost = typeof row.cost_usd === 'number' ? row.cost_usd : null;
    const record: UsageSummaryRecord = {
      input_tokens: tokens.input_tokens ?? null, cached_input_tokens: tokens.cached_input_tokens ?? null, cache_write_tokens: null,
      output_tokens: tokens.output_tokens ?? null, reasoning_output_tokens: tokens.reasoning_output_tokens ?? null, total_nano_aiu: null,
      cost_usd: cost, pricing_status: cost === null ? 'unpriced' : 'priced', premium_requests: null, premium_request_cost_usd: null,
    };
    const date = typeof row.ts === 'number' && Number.isFinite(row.ts) ? new Date(row.ts * 1000) : new Date(NaN);
    const dated = Number.isFinite(date.getTime());
    const keys = { by_role: typeof row.role === 'string' && row.role ? row.role : null,
      by_model: typeof row.model === 'string' && row.model ? row.model : null, by_date: dated ? date.toISOString().slice(0, 10) : null };
    for (const dimension of Object.keys(grouped) as Array<keyof typeof grouped>) {
      const group = grouped[dimension].get(keys[dimension]) ?? [];
      group.push(record);
      grouped[dimension].set(keys[dimension], group);
    }
    if (dated && typeof row.ms === 'number' && Number.isFinite(row.ms) && row.ms >= 0) {
      const stop = date.getTime();
      intervals.push([stop - row.ms, stop]);
    } else missing++;
    return record;
  });
  for (const call of calls) {
    const start = call.started_ts * 1000;
    if (Number.isFinite(start) && start <= now) intervals.push([start, now]);
  }
  intervals.sort((a, b) => a[0] - b[0]);
  let duration = 0, end = -Infinity;
  for (const [start, stop] of intervals) {
    duration += Math.max(0, stop - Math.max(start, end));
    end = Math.max(end, stop);
  }
  const groups = (dimension: keyof typeof grouped) => [...grouped[dimension]]
    .sort(([a], [b]) => (a ?? '').localeCompare(b ?? ''))
    .map(([key, values]) => ({ key, ...summarizeUsage(values) }));
  return { ...summarizeUsage(records), by_role: groups('by_role'), by_model: groups('by_model'), by_date: groups('by_date'),
    work_duration_ms: duration, calls_without_duration: missing };
}

/** The operator's messages a Manager turn is still to answer: each waits for the Manager till its
 * answer is handed on, and one a later message took the place of waits no more. */
const unanswered = (project: Project): Set<string> =>
  new Set((project.forManager ?? []).flatMap(letter => typeof letter === 'string' ? [] : letter.request_id ?? []));

/** Whether Resume has work to take up: letters to the Manager no turn took, failed turns, or the
 * open work and the Planner's letters the operator stopped. */
const resumable = (project: Project): boolean => Boolean(project.forManager?.length) || project.status === 'failed'
  || project.status === 'stopped' && (openTasks(project).length > 0 || project.inbox.length > 0);

/** Live calls joined to the request cards their answers belong to. */
function requestCalls(calls: Live['calls'], qa?: MapReading['qa']) {
  return calls.map(({ task, words: _, wordsKind: _kind, ...call }) => {
    const card = call.answers ? qa?.get(call.answers) : undefined;
    return { ...call, ...(task ? { task } : {}), ...(card ? { request_card_id: card.id } : {}) };
  });
}

export function daemon(project: Project, live: Live, qa?: MapReading['qa']): Daemon {
  const { since } = live;
  return {
    // Alive until interrupted calls finish recording their ends. Resume is no way on from a model
    // service that wants paying: it would fail again; once paid, a message takes the work up.
    alive: since !== null, resumable: since === null && resumable(project) && !project.failures?.some(failure => failure.unpaid), pid: since === null ? null : process.pid, uptime_seconds: since === null ? null : Date.now() / 1000 - since,
    started_at_iso: since === null ? null : new Date(since * 1000).toISOString(), backend: project.backend,
    backend_label: BACKEND_LABELS[project.backend],
    calls: requestCalls(live.calls, qa),
  };
}

/** Each task as the map draws it: after the tasks it waits for, and as it stands. */
function backlog(project: Project): BacklogSummary[] {
  const closed = new Set(project.tasks.filter(task => task.status === 'closed').map(task => task.id));
  return project.tasks.map(task => ({
    id: task.id, title: task.title, priority: 0, deps: task.after ?? [],
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
  /** Complete usage lines and the byte where their next read starts. */
  usage: { end: number; rows: Record<string, unknown>[]; summary: ProjectUsageSummary };
  fold: Fold;
  rows: Map<string, EventMsg>;
  roleEvents: EventMsg[];
  round: number;
  activityAt: number | null;
  /** First line of the last Manager letter addressed to the operator. */
  lastReply: string;
  stateLine: number;
  calls: Set<string>;
  tasks: Map<string, { title: string; line: number }>;
  /** Edit steps of each task's latest Engineer turn. */
  edits: Map<string, string[]>;
  questions: Map<string, EventMsg>;
  /** The results the Manager handed over, by delivery (`d` and its line), each with its files as delivered. */
  delivered: Map<string, Extract<Recorded, { type: 'handoff' }>>;
  map: MapReading;
  trace: { ended: Map<string, Omit<TraceGroup, 'entries'>>; tasks: Map<string, TraceGroup[]> };
}

export function projectRecord(events: Recorded[], reading: Reading = {
  end: 0, usage: { end: 0, rows: [], summary: usage([]) }, fold: newFold(), rows: new Map(),
  roleEvents: [], round: 0, activityAt: null, lastReply: '', stateLine: 0, calls: new Set(), tasks: new Map(), edits: new Map(), questions: new Map(), delivered: new Map(),
  trace: { ended: new Map(), tasks: new Map() },
  map: { added: new Map(), closed: new Map(), actions: new Map(), opened: new Map(), letters: [], qa: new Map(),
    answered: null, briefed: null, reported: null, worked: new Set(), follows: new Map() },
}): Reading {
  for (const event of events) {
    const line = reading.fold.line;
    const ts = seconds(event.ts);
    const converted = webEvents([event], reading.fold);
    foldMap(reading.map, event, line);
    foldTrace(reading.trace, event);
    if (event.type === 'call.start' && event.role === 'engineer' && event.task) reading.edits.set(event.task, []);
    if (event.type === 'step' && event.kind === 'edit' && event.role === 'engineer' && event.task) {
      const paths = reading.edits.get(event.task) ?? [];
      if (!paths.includes(event.text)) paths.push(event.text);
      reading.edits.set(event.task, paths);
    }
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
    } else if (event.type === 'call.start' && event.role === 'manager') {
      const call = reading.fold.calls.get(`manager.${event.task ?? ''}`)!;
      if (!reading.rows.has(call.reply)) put({ type: 'ui.argus', agent_layer: 'manager', text: '', ts,
        event_id: line, message_id: call.reply }, call.reply, call.order);
    } else if (event.type === 'step' && event.kind === 'message' && (event.role === 'manager' || event.role === 'planner')) {
      // Call ownership is independent of the role log's individual message rows.
      const call = reading.fold.calls.get(`${event.role}.${event.task ?? ''}`);
      const id = call?.reply ?? `m${line}-argus`;
      const reply = reading.rows.get(id);
      put({ ...reply, type: 'ui.argus', agent_layer: 'manager', ts: reply?.ts ?? ts,
        message_id: id, ...call?.answers ? { request_id: call.answers } : {},
        text: reply?.text ?? '', event_id: line, steps: [...(reply?.steps as string[] | undefined ?? []), event.text] },
        id, reply ? Number(reply.order) : call?.order ?? line);
    } else if (event.type === 'handoff' && event.to === 'operator' && event.role !== 'manager') {
      const row = { type: 'ui.question', ts, event_id: line, question_id: `q${line}`, agent_layer: event.role,
        item_id: event.task ?? '', text: event.letter, order: line };
      put(row, `q${line}`);
      reading.questions.set(asker({ role: event.role, task: event.task ?? null }), reading.rows.get(`q${line}`)!);
    } else if (event.type === 'handoff' && event.role === 'manager') {
      if (event.to === 'operator') reading.lastReply = firstLine(event.letter);
      const row = converted[0]!;
      const previous = reading.rows.get(String(row.message_id));
      put({ ...row, ...previous?.steps ? { steps: previous.steps } : {} }, String(row.message_id),
        previous ? Number(previous.order) : reading.fold.calls.get(`manager.${event.task ?? ''}`)?.order ?? line);
      const operatorId = String(row.message_id).replace(/-argus$/, '-operator');
      const operator = reading.rows.get(operatorId);
      if (operator) reading.rows.set(operatorId, { ...operator, answer: event.letter, event_id: line });
    }
    if (event.type === 'task.added') reading.tasks.set(event.task.id, { title: event.task.title, line });
    // A task's row comes after the last word sent it from above: the operator's answer to it, or the
    // Manager's or the Planner's letter to it.
    else if ((event.type === 'message' || event.type === 'handoff' && (event.role === 'manager' || event.role === 'planner')) && event.task) {
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
  const spent = jsonLines(join(store.dir(id), 'usage.jsonl'), reading.usage.end);
  reading.usage.rows.push(...spent.rows);
  if (spent.rows.length) reading.usage.summary = usage(reading.usage.rows);
  reading.usage.end = spent.end;
  readings.set(path, reading);
  return reading;
}

export function conversationRows(reading: Reading, project: Project, live: Live): EventMsg[] {
  const projected = new Map(reading.rows);
  const pending = unanswered(project);
  // Pending replies come from the complete dialogue, including messages interrupted before any words;
  // one sent after a stop waits on the turn before it.
  // A reply the Manager's failure held up says why, there alone.
  const failure = project.failures?.filter(f => f.role === 'manager').at(-1);
  for (const [request, line] of reading.fold.asked) {
    const operator = reading.rows.get(`m${line}-operator`)!;
    const liveReply = live.calls.find(call => call.answers === request);
    const status = liveReply ? 'running' : !pending.has(request) ? 'replaced' : live.requests.includes(request) ? 'waiting'
      : project.status === 'stopped' ? 'stopped' : failure ? 'failed' : 'waiting';
    projected.set(`m${line}-argus`, { ...reading.rows.get(`m${line}-argus`), type: 'ui.argus', row_id: `m${line}-argus`, message_id: `m${line}-argus`, agent_layer: 'manager',
      request_id: operator.request_id, ts: Number(operator.ts), event_id: Math.max(reading.stateLine, Number(reading.rows.get(`m${line}-argus`)?.event_id ?? line)), order: line + 0.1,
      steps: [...(reading.rows.get(`m${line}-argus`)?.steps as string[] | undefined ?? []),
        ...liveReply?.words && liveReply.wordsKind !== 'answer' ? [liveReply.words] : []],
      text: status === 'failed' ? failure!.error : liveReply?.wordsKind === 'answer' ? liveReply.words ?? '' : '', live: status === 'running' || status === 'waiting', status });
  }
  for (const call of live.calls.filter(call => !call.answers && (call.role === 'manager' || call.role === 'planner'))) {
    const owner = reading.fold.calls.get(`${call.role}.${call.task ?? ''}`);
    if (!owner) continue;
    const previous = projected.get(owner.reply);
    if (call.role === 'planner' && !call.words) continue;
    projected.set(owner.reply, { ...previous, type: 'ui.argus', agent_layer: 'manager', row_id: owner.reply,
      message_id: owner.reply, order: previous?.order ?? owner.order, ts: previous?.ts ?? call.started_ts,
      event_id: Math.max(reading.stateLine, Number(previous?.event_id ?? 0)),
      steps: [...(previous?.steps as string[] | undefined ?? []), ...call.words && call.wordsKind !== 'answer' ? [call.words] : []],
      text: call.role === 'manager' && call.wordsKind === 'answer' ? call.words ?? '' : previous?.text ?? '' });
  }
  const rows = [...projected.values()];
  const items = backlog(project);
  for (const [id, task] of reading.tasks) {
    const item = items.find(item => item.id === id);
    const { role, words } = live.calls.find(call => call.task === id) ?? {};
    // What was last said on it, unless it waits for an answer, and once it has closed, its result.
    const { latest_action: action, result_paths } = taskPresentation(reading.map, project, id);
    const state = item?.status === 'done' ? 'done' : item?.status === 'blocked' ? 'asking'
      : item?.status === 'failed' ? 'failed' : project.status === 'stopped' ? 'paused'
      : item?.status === 'pending' ? 'queued' : live.since !== null ? 'running' : 'paused';
    rows.push({ type: 'ui.task', row_id: `task-${id}`, item_id: id, title: task.title, state, role,
      progress: firstLine(words || action?.text || ''), result_paths,
      ts: reading.map.added.get(id)!, event_id: Math.max(reading.stateLine, reading.map.actions.get(id)?.line ?? 0), order: task.line + 0.2 });
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

/** The project's one status, and when anything last happened in it. */
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
  const reading = readRecord(store, id);
  const spent = reading.usage.rows;
  const at = readAt();
  const summary = live.calls.length ? usage(spent, live.calls, at) : reading.usage.summary;
  const { calls: made } = reading;
  const calls = spent.filter(call => made.has(String(call.callId)));
  // The model each role's last call ran on: with none chosen, the backend's own default.
  const ran = (role?: Role) => calls.filter(call => !role || call.role === role).at(-1);
  const last = ran();
  return {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    read_at: at,
    session: {
      id, display_name: named(project), objective: project.request, created: seconds(project.created),
      last_active: lastActive(store, id), workdir,
    },
    daemon: daemon(project, live, reading.map.qa),
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
      .map(request_id => ({ request_id, status: live.calls.some(call => call.answers === request_id) ? 'running' as const : 'waiting' as const })),
  };
}

export function projectRow(store: Store, project: Project, live: Live): ProjectRow {
  const { since } = live;
  const reading = readRecord(store, project.id);
  const summary = reading.usage.summary;
  return {
    // A label other than the id is the name the operator gave it.
    id: project.id, label: project.name || project.id, display_name: named(project),
    workdir: project.workdir, last_active: lastActive(store, project.id),
    daemon_alive: since !== null, daemon_pid: since === null ? null : process.pid,
    work_status: projectStatus(project, live, reading.activityAt),
    last_message: project.status === 'failed' ? project.error ?? '' : reading.lastReply,
    spend_usd: summary.cost_usd, spend_status: summary.pricing_status as NonNullable<ProjectRow['spend_status']>,
  };
}

type MapCard = Omit<BacklogItem, 'objective' | 'notes' | 'last_error'> & { role: string; turn_kind?: 'qa' };

/** Map facts in the shared project reading: when each task
 * was added and closed, the latest thing said on it (a message or a letter, not a command), the
 * first result the Manager handed over for their request, the letters written about
 * tasks, by line, the operator's messages to the Manager, and which tasks follow the message whose
 * answer set them going. */
interface MapReading {
  added: Map<string, number>;
  closed: Map<string, number>;
  actions: Map<string, NonNullable<BacklogItem['latest_action']> & { line: number; preview: string }>;
  /** Internal task selections, read only by the full task detail endpoint. */
  opened: Map<string, string[]>;
  letters: { line: number; letter: Record<string, unknown>; preview: string }[];
  /** Requests to the Manager; initial clarifications share the original request card. */
  qa: Map<string, MapCard>;
  /** The message the Manager's turn under way has answered, whose tasks follow it; and the one
   * whose answer last briefed the Planner, whose tasks follow it. */
  answered: string | null;
  briefed: string | null;
  /** The message whose work the team last reported on to the Manager: the Manager's report on it,
   * which names no message, answers it. */
  reported: string | null;
  /** The messages whose answer handed work to the team or handed over files. */
  worked: Set<string>;
  follows: Map<string, string>;
}

/** The progress map: a card per task, and a card for each operator request that starts work or
 * hands over files. Clarifications before the original request starts work share its card,
 * which always stays. Request ids and the Manager's `answers` join the letters to their card,
 * and task handoffs join work to the request that set it going. The line cursor pages drawing
 * summaries; the task detail reader retains each original letter. */
export function progressMap(store: Store, project: Project, after: number, calls: Live['calls']) {
  return mapProjection(readRecord(store, project.id), project, after, calls);
}

/** Only the first nonempty heading is needed on the canvas. Stop before the body, even
 * for a multiline letter; compute it once when the shared reading folds that letter. */
function mapExcerpt(text: string): string {
  for (const line of text.matchAll(/[^\n]+/g)) {
    const heading = firstLine(line[0]);
    if (heading) return heading.length > 160 ? `${heading.slice(0, 159).trimEnd()}…` : heading;
  }
  return '';
}

function foldMap(reading: MapReading, event: Recorded, line: number): void {
  const { added, closed, actions, opened, letters, qa, worked, follows } = reading;
  const ts = seconds(event.ts);
  const letter = (value: Record<string, unknown>) => letters.push({ line, letter: value, preview: mapExcerpt(String(value.text)) });
  const action = (id: string, role: string, text: string) => actions.set(id, { role, text, ts, line, preview: mapExcerpt(text) });
  if (event.type === 'message' && !event.to && event.request_id) {
    const previous = [...qa.values()].at(-1);
    // Before the original request starts work or delivers files, clarification extends that
    // request. Later exchanges keep their own cards, as do requests queued before any reply.
    if (previous === qa.values().next().value && previous?.status === 'done' && !worked.has(previous.id)) {
      qa.set(event.request_id, previous);
      Object.assign(previous, { status: '', finished_ts: null });
      actions.delete(previous.id);
      letter({ id: `e${line}`, item_id: previous.id, type: 'turn.asked', ts, role: 'operator', to: 'manager', text: event.text });
    } else qa.set(event.request_id, {
      id: `q${line}`, turn_kind: 'qa', title: firstLine(event.text), priority: 0, deps: [], status: '',
      role: 'manager', ts, started_ts: ts, finished_ts: null, result_paths: [],
    });
  }
  else if (event.type === 'message' && event.to && event.task) {
    letter({ id: `e${line}`, item_id: event.task, type: 'handoff', ts, role: 'operator', to: event.to, text: event.text });
  }
  else if (event.type === 'call.start' && event.role === 'manager') reading.answered = null;
  else if (event.type === 'task.added') {
    added.set(event.task.id, ts);
    const follow = event.task.from === 'manager' ? reading.answered : reading.briefed;
    if (follow) follows.set(event.task.id, follow);
  }
  else if (event.type === 'task.closed') closed.set(event.task, ts);
  else if (event.type === 'step' && event.task && event.kind === 'message') action(event.task, event.role, event.text);
  else if (event.type === 'handoff') {
    if (event.to === 'manager') reading.reported = event.task ? follows.get(event.task) ?? null : event.role === 'planner' ? reading.briefed : null;
    if (event.task) {
      if (event.role === 'engineer' || event.role === 'reviewer') {
        if (event.to === 'manager' || event.to === 'planner' || event.open !== undefined) opened.set(event.task, event.open ?? []);
        if (event.to !== 'operator') action(event.task, event.role, event.letter);
      }
      letter({ id: `e${line}`, item_id: event.task, type: 'handoff', ts, role: event.role, to: event.to, text: event.letter });
    }
    if (event.role === 'manager' && event.answers && qa.has(event.answers)) {
      const card = qa.get(event.answers)!;
      // A reply answers the message; work handed to the team answers it once that work is done.
      const replied = event.to === 'operator';
      Object.assign(card, { status: replied ? 'done' : 'in_progress', finished_ts: replied ? ts : null, result_paths: (event.open ?? card.result_paths ?? []).slice(0, 1) });
      // Word for a task under way joins the message to that task.
      if (event.task && added.has(event.task) && !closed.has(event.task)) card.deps = [event.task];
      reading.answered = card.id;
      if (event.to === 'planner') reading.briefed = card.id;
      if (event.to !== 'operator' || event.open?.length) worked.add(card.id);
      // The reply is the message's progress; a brief to the team is not: its tasks show theirs.
      if (replied) action(card.id, event.role, event.letter);
      if (replied) letter({ id: `e${line}${event.task ? ':reply' : ''}`, item_id: card.id, type: 'turn.replied', ts, role: event.role, to: event.to, text: event.letter });
    } else if (event.role === 'manager' && event.to === 'operator' && reading.reported) {
      const card = [...qa.values()].find(card => card.id === reading.reported);
      if (card && event.open) card.result_paths = event.open.slice(0, 1);
      action(reading.reported, event.role, event.letter);
      letter({ id: `e${line}`, item_id: reading.reported, type: 'turn.replied', ts, role: event.role, to: event.to, text: event.letter });
    }
  }
}

/** Dialogue and map share progress and the Manager's primary result for the task's request.
 * Internal task files remain in the full letter reader. */
export function taskPresentation(reading: MapReading, project: Project, id: string, preview = false) {
  const question = asked(project, id);
  const action = question ? undefined : reading.actions.get(id);
  const request = [...reading.qa.values()].find(card => card.id === reading.follows.get(id));
  return {
    latest_action: action ? { role: action.role, text: preview ? action.preview : action.text, ts: action.ts } : undefined,
    result_paths: reading.closed.has(id) ? request?.result_paths ?? [] : [],
  };
}

export function mapProjection(record: Reading, project: Project, after: number, calls: Live['calls']) {
  const { added, closed, letters, qa, worked, follows } = record.map;
  // An answered message stays if it is the original request, the latest one, or set work going; one
  // still to be answered while the team answers, and the original request even while it is stopped.
  const answering = project.status !== 'stopped' && project.status !== 'failed';
  const cards = [...new Set(qa.values())];
  const first = (project.requestId && qa.get(project.requestId)) || cards[0];
  const last = cards.at(-1);
  const waiting = new Set([...unanswered(project)].flatMap(request => qa.get(request) ?? []));
  // The Manager may answer a new message while the team stays stopped. Keep that call's
  // request card until the call ends; the usual filter still removes replaced requests.
  const active = new Set(requestCalls(calls, qa).flatMap(call => call.role === 'manager' ? call.request_card_id ?? [] : []));
  // Work handed to the team is done once the tasks it went to, or that follow its message, have
  // closed, or with none of its own, once the team has no work going.
  const done = (card: MapCard): number | null => {
    const own = [...card.deps ?? [], ...[...follows].flatMap(([task, follow]) => follow === card.id ? [task] : [])];
    if (own.length ? own.some(task => !closed.has(task)) : project.status !== 'idle') return null;
    return Math.max(card.ts ?? 0, ...own.map(task => closed.get(task)!));
  };
  const tasks = [...cards.filter(card => active.has(card.id) || (card.status ? card === first || card === last || worked.has(card.id)
    : waiting.has(card) && (answering || card === first))).map(card => {
    const finished = card.status === 'in_progress' ? done(card) : card.finished_ts;
    return { ...card, status: card.status && finished !== null ? 'done' : 'in_progress', finished_ts: finished, latest_action: taskPresentation(record.map, project, card.id, true).latest_action };
  }), ...backlog(project).map((item, index) => {
    const follow = follows.get(item.id);
    return {
      ...item, deps: item.deps?.length || !follow ? item.deps : [follow], ts: added.get(item.id), started_ts: added.get(item.id),
      finished_ts: closed.get(item.id) ?? null, role: project.tasks[index]!.holder,
      ...taskPresentation(record.map, project, item.id, true),
    };
  })].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
  const shown = new Set(tasks.map(task => task.id));
  return { id: `live:${project.id}`, tasks,
    events: letters.flatMap(({ line, letter, preview }) => line >= after && shown.has(String(letter.item_id))
      ? [{ ...letter, text: preview } as Record<string, unknown>] : []), cursor: String(record.fold.line) };
}

interface TraceGroup {
  call_id: string;
  role: Role;
  started_ts: number;
  ended_ts: number;
  entries: { kind: string; text: string }[];
}

/** Retain the call facts needed by traces in the shared fold. Letters on a task belong to the last
 * completed call in their author's scope; the brief that opened it is the task's objective. */
function foldTrace(trace: Reading['trace'], event: Recorded): void {
  const include = (task: string, call: Omit<TraceGroup, 'entries'>): TraceGroup => {
    let groups = trace.tasks.get(task);
    if (!groups) trace.tasks.set(task, groups = []);
    let found = groups.find(group => group.call_id === call.call_id);
    if (!found) groups.push(found = { ...call, entries: [] });
    return found;
  };
  if (event.type === 'call.end') {
    const call = { call_id: event.callId, role: event.role, started_ts: seconds(event.ts) - event.ms / 1000, ended_ts: seconds(event.ts) };
    trace.ended.set(`${event.role}.${event.task ?? ''}`, call);
    if (event.task) include(event.task, call);
  } else if (event.type === 'handoff' && event.task) {
    const scope = event.role === 'engineer' || event.role === 'reviewer' ? event.task : '';
    const call = trace.ended.get(`${event.role}.${scope}`);
    if (call) include(event.task, call).entries.push({ kind: 'message', text: event.letter });
  }
}

/** A task's calls and incoming letters, grouped by the call that wrote them.
 * Its brief is not among them: the reader shows it as the objective. */
export function taskTrace(store: Store, project: Project, task: string) {
  return { task_id: task, groups: readRecord(store, project.id).trace.tasks.get(task) ?? [] };
}
