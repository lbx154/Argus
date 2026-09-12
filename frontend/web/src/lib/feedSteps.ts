// The live feed, told the way a person would tell it.
//
// The daemon writes one event per beat: a tool call, then the same call again
// with its outcome; five file reads in a row; the same failure sentence once
// per retry. Rendered one row per event the feed reads like a log. These
// passes fold it back into acts:
//
//   1. a call and its outcome become one row (`pairCalls`);
//   2. a sentence repeated in a row becomes one row with "×N", and a retry
//      cycle whose rows alternate ("planning… / the Planner hit an error")
//      collapses the same way (`collapseRepeats`);
//   3. three or more consecutive calls of the same tool fold into one line —
//      "Read 5 files: a.py, b.py, c.py…" — that expands back to its members
//      (`groupTools`). Two reads are still two lines worth reading; shell
//      commands never fold, because `pytest` and `git status` are two
//      different things that happened.
//
// Streaming message fragments are coalesced before any of this runs, in
// `renderFeedRows`, so a reply that is still being typed stays one growing row.

import type { EventMsg } from '../api';
import type { Locale } from '../i18n';
import { renderLine, type RenderedLine } from '../../../core/src/eventRender';
import {
  eventKey,
  eventMatchesView,
  fragmentMode,
  isReasoning,
  mergeFragment,
  type EventViewFilter,
} from '../../../core/src/events';
import { plainDetail } from './plainStatus';

export interface FeedRowInput {
  ev: EventMsg;
  r: RenderedLine;
  key: string;
}

export type ToolAction = 'read' | 'search' | 'fetch' | 'edit' | 'tool';
export type StepStatus = 'running' | 'completed' | 'failed' | '';

/** One thing that happened. `ev` opened the row; `latest` is the event that last touched it. */
export interface FeedStep {
  kind: 'step';
  key: string;
  ev: EventMsg;
  r: RenderedLine;
  latest: EventMsg;
  /** How many identical rows this one stands for; 1 means it is shown plainly. */
  repeat: number;
  status: StepStatus;
  /** The tool's output, when the stream sent it as a row of its own. */
  result?: FeedRowInput;
}

/** Consecutive calls of one tool, folded into a sentence. Expands back to its steps. */
export interface FeedGroup {
  kind: 'group';
  key: string;
  r: RenderedLine;
  latest: EventMsg;
  tool: string;
  action: ToolAction;
  steps: FeedStep[];
  /** Distinct files, pages or patterns the calls touched, in order of first use. */
  targets: string[];
  /** The number the summary sentence says: files when every call names one, calls otherwise. */
  count: number;
  /** Calls the group stands for, repeats included. */
  calls: number;
  failed: number;
}

export type FeedRow = FeedStep | FeedGroup;

/** Three, not two: two file reads are still two lines worth reading, five are one act. */
export const MIN_GROUP_SIZE = 3;
const TARGETS_SHOWN = 3;

const PROGRESS = 'engineer.progress';
const CALL_KINDS = new Set(['tool_use', 'command_execution', 'file_change']);
const MESSAGE_KINDS = ['assistant_message', 'agent_message', 'message'];

const rec = (ev: EventMsg) => ev as Record<string, unknown>;
const field = (ev: EventMsg, key: string) => String(rec(ev)[key] ?? '');
const progressKind = (ev: EventMsg) => (field(ev, 'type') === PROGRESS ? field(ev, 'kind') : '');

// ── 0. events → rows ────────────────────────────────────────────────────────

export interface RenderFeedOptions {
  locale: Locale;
  showReasoning: boolean;
  filter?: EventViewFilter;
  query?: string;
}

/**
 * Render, whitelist and coalesce streaming fragments once per change.
 * Every event goes through the shared renderer (`frontend/core/src/eventRender`),
 * so the web feed shows the same line as the terminal; what it hides, this
 * feed hides. engineer.progress message events stream in fragments sharing a
 * message_id (replace=True); we keep the growing text at its first position
 * so a streaming reply is ONE growing row, not a char-by-char flood.
 */
export function renderFeedRows(
  events: EventMsg[],
  { locale, showReasoning, filter = 'all', query = '' }: RenderFeedOptions,
): { list: FeedRowInput[]; hiddenReasoning: number } {
  const out: FeedRowInput[] = [];
  const msgRow = new Map<string, number>(); // message_id → index in out
  let hiddenReasoning = 0;
  const context = { locale, showReasoning, unknownEventPolicy: 'hide', density: 'compact' } as const;
  events.forEach((ev) => {
    const r = renderLine(ev, context);
    if (!r) {
      if (!showReasoning && isReasoning(ev)) hiddenReasoning++;
      return; // noise, or reasoning the reader switched off
    }
    if (!eventMatchesView(ev, r, filter, query)) return;
    const mid = field(ev, 'message_id');
    const isMsg = !!mid && field(ev, 'type') === PROGRESS && MESSAGE_KINDS.includes(field(ev, 'kind'));
    if (isMsg && msgRow.has(mid)) {
      const idx = msgRow.get(mid)!;
      // grow the streaming message (merge blocks) instead of dropping shorter
      // fragments — a multi-block reply must not look truncated.
      out[idx] = {
        ...out[idx],
        ev: { ...out[idx].ev, ...ev },
        r: {
          ...out[idx].r,
          ...r,
          text: mergeFragment(out[idx].r.text, r.text, fragmentMode(ev)),
        },
      };
      return;
    }
    const entry = { ev, r, key: eventKey(ev) };
    if (isMsg) msgRow.set(mid, out.length);
    out.push(entry);
  });
  return { list: out, hiddenReasoning };
}

// ── tool calls ───────────────────────────────────────────────────────────────

/** The tool a progress row is about: the explicit name, else the `name: {…}` prefix of its text. */
export function toolName(ev: EventMsg): string {
  const explicit = field(ev, 'tool_name') || field(ev, 'tool') || field(ev, 'name');
  if (explicit) return explicit;
  const match = field(ev, 'text').match(/^([A-Za-z_][\w.-]{0,40}):\s/);
  return match ? match[1] : '';
}

function callStatus(ev: EventMsg): StepStatus {
  const status = field(ev, 'status').toLowerCase();
  if (status === 'running' || status === 'started' || status === 'in_progress' || status === 'pending') return 'running';
  if (status === 'failed' || status === 'error' || status === 'errored') return 'failed';
  const exit = rec(ev).exit_code;
  if (typeof exit === 'number' && exit !== 0) return 'failed';
  if (['completed', 'complete', 'success', 'succeeded', 'done', 'ok', 'finished'].includes(status)) return 'completed';
  return '';
}

function sameCall(step: FeedStep, ev: EventMsg): boolean {
  const callId = field(ev, 'call_id');
  const stepCallId = field(step.ev, 'call_id');
  if (callId && stepCallId) return callId === stepCallId;
  return (
    progressKind(step.ev) === progressKind(ev)
    && toolName(step.ev) === toolName(ev)
    && field(step.ev, 'text') === field(ev, 'text')
  );
}

const asStep = (row: FeedRowInput, status: StepStatus): FeedStep => ({
  kind: 'step',
  key: row.key,
  ev: row.ev,
  r: row.r,
  latest: row.ev,
  repeat: 1,
  status,
});

/**
 * Fold a call and its outcome into one row.
 *
 * Two shapes reach the feed. Copilot reports a call once as `running` and again,
 * with the same text, as `failed` (a success is not echoed); other calls of the
 * same batch can sit between the two, so the outcome goes to the nearest still
 * open call that matches. The ACP path sends the output as a `tool_result` row
 * with a `call_id`; without one it belongs to the oldest open call of that tool.
 * A result whose call was never seen stays a row of its own, so no output is lost.
 */
export function pairCalls(rows: FeedRowInput[]): FeedStep[] {
  const steps: FeedStep[] = [];
  const open: FeedStep[] = []; // calls still running, oldest first
  const close = (index: number, ev: EventMsg, status: StepStatus, result?: FeedRowInput) => {
    const [step] = open.splice(index, 1);
    step.status = status;
    step.latest = ev;
    if (result) step.result = result;
  };
  for (const row of rows) {
    const kind = progressKind(row.ev);
    if (CALL_KINDS.has(kind)) {
      const status = callStatus(row.ev);
      if (status !== 'running') {
        let index = open.length - 1;
        while (index >= 0 && !sameCall(open[index], row.ev)) index -= 1;
        if (index >= 0) {
          close(index, row.ev, status || 'completed');
          continue;
        }
      }
      const step = asStep(row, status);
      steps.push(step);
      if (status === 'running') open.push(step);
      continue;
    }
    if (kind === 'tool_result') {
      const callId = field(row.ev, 'call_id');
      const tool = toolName(row.ev);
      const index = callId
        ? open.findIndex((step) => field(step.ev, 'call_id') === callId)
        : open.findIndex((step) => !tool || toolName(step.ev) === tool);
      if (index >= 0) {
        close(index, row.ev, callStatus(row.ev) || 'completed', row);
        continue;
      }
    }
    steps.push(asStep(row, ''));
  }
  return steps;
}

// ── repeats and retry cycles ────────────────────────────────────────────────

const FAILURE_TYPE = /fail|error|backoff|stall|denied|blocked|nudge|escalat/i;
const FAILURE_WORDS = /\b(?:fail(?:ed|ure)?|error|unavailable|unreachable|exited with code|backoff|retry(?:ing)?|stuck|skipped)\b/i;

/** A lifecycle row that reports something going wrong; tool calls and agent speech are never streak rows. */
function isFailure(step: FeedStep): boolean {
  if (progressKind(step.ev)) return false;
  const ev = step.ev;
  return (
    step.r.tone === 'err'
    || step.r.tone === 'warn'
    || FAILURE_TYPE.test(field(ev, 'type'))
    || rec(ev).backend_unavailable === true
    || rec(ev).review_skipped === true
    || FAILURE_WORDS.test(step.r.text)
  );
}

/** Numbers vary between retries ("1/2", "attempt 3", "after 15.0s"); the cause does not. */
const withoutNumbers = (text: string) => text.toLowerCase().replace(/\d+(?:\.\d+)?/g, '#').replace(/\s+/g, ' ').trim();

function failureCause(step: FeedStep): string {
  const ev = step.ev;
  const cause = field(ev, 'signature')
    || field(ev, 'cause')
    || field(ev, 'backend_error')
    || field(ev, 'error')
    || field(ev, 'reason')
    || field(ev, 'stop_kind')
    || step.r.text;
  return withoutNumbers(cause);
}

/** What the reader would see twice: the same role saying the same sentence, or failing for the same cause. */
function signature(step: FeedStep, locale: Locale): string {
  if (isFailure(step)) return `fail|${step.r.role}|${field(step.ev, 'type')}|${failureCause(step)}`;
  return `text|${step.r.role}|${step.r.glyph}|${plainDetail(step.r.text, locale).text}`;
}

/** `into` keeps its opening event and key; everything the reader sees comes from the newest repeat. */
function absorb(into: FeedStep, step: FeedStep) {
  into.repeat += step.repeat;
  into.latest = step.latest;
  into.r = step.r;
  if (step.status) into.status = step.status;
  if (step.result) into.result = step.result;
}

/**
 * Consecutive identical rows become one row with a count; a cycle of two or
 * three rows that repeats as a block collapses to that block with a count on
 * each row. Cycles are compared with their numbers blanked, so a retry loop
 * ("planning X" / "the Planner hit an error", once per attempt) and a run of
 * rounds in which nothing else happened ("round 4" / "finished round 4 of
 * work", then 5, then 6) each fold to two rows. Rounds with work between them
 * are not consecutive blocks and stay as they were.
 */
export function collapseRepeats(steps: FeedStep[], locale: Locale): FeedStep[] {
  const out: FeedStep[] = [];
  const sigs: string[] = [];
  for (const step of steps) {
    const sig = signature(step, locale);
    const previous = out[out.length - 1];
    if (previous && sigs[sigs.length - 1] === sig) {
      absorb(previous, step);
      continue;
    }
    out.push({ ...step });
    sigs.push(sig);
  }

  // Numbers are blanked only for lifecycle rows ("round 4", "…streak=2/2"):
  // what the agent said or ran is compared word for word, so "part 1 of the
  // proof" and "part 2 of the proof" stay two different things that happened.
  const cycleSigs = sigs.map((sig, index) => (progressKind(out[index].ev) ? sig : withoutNumbers(sig)));
  const blockRepeats = (at: number, period: number) => {
    if (at + 2 * period > out.length) return false;
    for (let offset = 0; offset < period; offset += 1) {
      if (cycleSigs[at + offset] !== cycleSigs[at + period + offset]) return false;
    }
    return true;
  };
  let at = 0;
  while (at < out.length) {
    let period = 0;
    for (const candidate of [2, 3]) {
      if (blockRepeats(at, candidate)) {
        period = candidate;
        break;
      }
    }
    if (!period) {
      at += 1;
      continue;
    }
    while (blockRepeats(at, period)) {
      for (let offset = 0; offset < period; offset += 1) absorb(out[at + offset], out[at + period + offset]);
      out.splice(at + period, period);
      cycleSigs.splice(at + period, period);
    }
    at += period;
  }
  return out;
}

// ── tool groups ─────────────────────────────────────────────────────────────

const ACTION_BY_TOOL: Array<[RegExp, ToolAction]> = [
  [/^(view|read|read_?files?|cat|open|open_?file|notebook_?read|get_?file)$/, 'read'],
  [/^(rg|grep|glob|find|search|ls|list|list_?dir|list_?files|codebase_?search|file_?search|semantic_?search)$/, 'search'],
  [/^(web_?fetch|fetch|web_?search|http|browse|browser|curl|get_?url|search_?web)$/, 'fetch'],
  [/^(apply_?patch|edit|write|create|create_?file|str_?replace|str_?replace_?editor|multi_?edit|patch|write_?file|notebook_?edit|save)$/, 'edit'],
];

export function toolAction(tool: string, kind: string): ToolAction {
  if (kind === 'file_change') return 'edit';
  const name = tool.toLowerCase();
  for (const [pattern, action] of ACTION_BY_TOOL) if (pattern.test(name)) return action;
  return 'tool';
}

/** Which consecutive rows may fold together: one key per tool; shell commands and everything else never. */
function foldKey(step: FeedStep): string | null {
  const kind = progressKind(step.ev);
  if (kind === 'tool_use') return `tool:${toolName(step.ev).toLowerCase() || 'tool'}`;
  if (kind === 'file_change') return 'file';
  return null;
}

const basename = (path: string) => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path;
const clip = (text: string, n: number) => (text.length <= n ? text : `${text.slice(0, n - 1).trimEnd()}…`);
// The query often IS the page (openreview.net/forum?id=…), so only the fragment goes.
const shortUrl = (url: string) => clip(url.replace(/^[a-z]+:\/\/(?:www\.)?/i, '').replace(/#.*$/, '').replace(/\/$/, ''), 48);
const PATCH_FILE = /\*\*\* (?:Add|Update|Delete|Move to) File: ([^\n]+)/;

/** A named argument, read from parsed JSON or, when the text was cut short, from the raw string. */
function argument(parsed: Record<string, unknown> | null, raw: string, keys: string[]): string {
  for (const key of keys) {
    if (parsed) {
      const value = parsed[key];
      if (typeof value === 'string' && value.trim()) return value.trim();
      if (Array.isArray(value) && typeof value[0] === 'string' && value[0].trim()) return value[0].trim();
      continue;
    }
    const match = raw.match(new RegExp(`"${key}":\\s*"((?:[^"\\\\]|\\\\.)*)"`));
    if (match && match[1]) return match[1].replace(/\\(.)/g, '$1');
  }
  return '';
}

/** The file, page or pattern a call was about — the word that makes "Read 5 files" specific. */
export function stepTarget(step: FeedStep): string {
  return toolTarget(step.ev);
}

export function toolTarget(ev: EventMsg): string {
  const text = field(ev, 'text');
  if (progressKind(ev) === 'file_change') {
    const patch = text.match(PATCH_FILE);
    if (patch) return basename(patch[1].trim());
    const path = text.match(/^(\S+\.[A-Za-z0-9]{1,8})\b/);
    return path ? basename(path[1]) : '';
  }
  const args = text.replace(/^[A-Za-z_][\w.-]{0,40}:\s*/, '');
  let parsed: Record<string, unknown> | null = null;
  if (args.startsWith('{')) {
    try {
      const value: unknown = JSON.parse(args);
      parsed = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
    } catch {
      parsed = null;
    }
  }
  const file = argument(parsed, args, ['path', 'file_path', 'filePath', 'file', 'filename', 'target_file', 'notebook_path', 'paths']);
  if (file && !/^\.\/?$/.test(file)) return basename(file);
  const url = argument(parsed, args, ['url', 'uri']);
  if (url) return shortUrl(url);
  const pattern = argument(parsed, args, ['pattern', 'query', 'regex', 'glob', 'q']);
  if (pattern) return clip(pattern, 32);
  const patch = args.match(PATCH_FILE);
  if (patch) return basename(patch[1].trim());
  return '';
}

/** A tool action in the main status line; its full arguments stay in the record. */
export function readableToolProgress(event: EventMsg, locale: Locale): { title: string; detail: string } | null {
  const kind = progressKind(event);
  if (!['tool_use', 'file_change'].includes(kind)) return null;
  const action = toolAction(toolName(event), kind);
  const labels: Record<ToolAction, [string, string]> = {
    read: ['查看资料', 'Reading material'], search: ['查找相关信息', 'Searching for relevant information'],
    fetch: ['读取资料页面', 'Reading a source page'], edit: ['更新项目文件', 'Updating project files'],
    tool: ['执行一个工作步骤', 'Carrying out a work step'],
  };
  return { title: labels[action][locale === 'zh-CN' ? 0 : 1], detail: toolTarget(event) };
}

function makeGroup(run: FeedStep[]): FeedGroup {
  const first = run[0];
  const last = run[run.length - 1];
  const tool = toolName(first.ev) || (progressKind(first.ev) === 'file_change' ? 'file' : 'tool');
  const action = toolAction(tool, progressKind(first.ev));
  const targets: string[] = [];
  let named = 0;
  for (const step of run) {
    const target = stepTarget(step);
    if (!target) continue;
    named += 1;
    if (!targets.includes(target)) targets.push(target);
  }
  const calls = run.reduce((total, step) => total + step.repeat, 0);
  const countsThings = action === 'read' || action === 'edit' || action === 'fetch';
  return {
    kind: 'group',
    key: `group:${first.key}`,
    r: { ...first.r, text: '' },
    latest: last.latest,
    tool,
    action,
    steps: run,
    targets,
    count: countsThings && named === run.length ? targets.length : calls,
    calls,
    failed: run.filter((step) => step.status === 'failed').length,
  };
}

/** Fold runs of `MIN_GROUP_SIZE`+ consecutive same-tool calls into one row each. */
export function groupTools(steps: FeedStep[]): FeedRow[] {
  const rows: FeedRow[] = [];
  let run: FeedStep[] = [];
  let runKey: string | null = null;
  const flush = () => {
    if (run.length >= MIN_GROUP_SIZE) rows.push(makeGroup(run));
    else rows.push(...run);
    run = [];
    runKey = null;
  };
  for (const step of steps) {
    const key = foldKey(step);
    if (key && (runKey === null || runKey === key)) {
      run.push(step);
      runKey = key;
      continue;
    }
    flush();
    if (key) {
      run.push(step);
      runKey = key;
    } else {
      rows.push(step);
    }
  }
  flush();
  return rows;
}

// ── the whole fold ──────────────────────────────────────────────────────────

/** Rows as displayed → rows as a person would tell them. Pure; safe to call on every change. */
export function foldFeedRows(rows: FeedRowInput[], locale: Locale): FeedRow[] {
  return groupTools(collapseRepeats(pairCalls(rows), locale));
}

/** Every step inside a row, so a group and a lone step read the same way. */
export function rowSteps(row: FeedRow): FeedStep[] {
  return row.kind === 'group' ? row.steps : [row];
}

type Translate = (key: string, variables?: Record<string, string | number>) => string;

/** The one line a folded group shows: "Read 5 files: a.py, b.py, c.py…" */
export function groupSummary(group: FeedGroup, t: Translate, locale: Locale): string {
  const head = t(`stream.fold.${group.action}`, { count: group.count, tool: group.tool });
  const shown = group.targets.slice(0, TARGETS_SHOWN);
  const list = shown.length
    ? `${locale === 'zh-CN' ? '：' : ': '}${shown.join(locale === 'zh-CN' ? '、' : ', ')}${group.targets.length > shown.length ? '…' : ''}`
    : '';
  const failed = group.failed ? ` · ${t('stream.fold.failed', { count: group.failed })}` : '';
  return `${head}${list}${failed}`;
}

/** The phrase a collapsed role header shows for its newest row. */
export function rowPreview(row: FeedRow, t: Translate, locale: Locale): string {
  if (row.kind === 'group') return groupSummary(row, t, locale);
  return plainDetail(row.r.text, locale).text;
}
