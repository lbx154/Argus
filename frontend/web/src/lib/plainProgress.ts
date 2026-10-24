import type { EventMsg } from '../api';
import type { RenderedLine } from '../../../core/src/eventRender';
import type { Locale } from '../i18n';
import { readableRecord } from '../map/submap';
import { plainToolLabel } from './feedSteps';
import { plainDetail, type PlainDetail } from './plainStatus';

/**
 * The project work progress feed in the words of the person who asked for the
 * work (docs: ux-principles rule 1). Each role reads as what it said or
 * concluded; the commands it ran on the way, the full text of a verdict and the
 * host's own English reasons are folded away or said in the reader's language.
 */

type Row = { ev: EventMsg; r: RenderedLine };
export type ProgressText = PlainDetail & {
  /** Everything the row stands for, shown on request when `text` is its one-line form. */
  full?: string;
};

/** What a role does on the way: shell commands, tool calls, their output and file edits. */
const CALL_KINDS = new Set(['command_execution', 'tool_use', 'tool_result', 'file_change']);
const MESSAGE_KINDS = new Set(['agent_message', 'assistant_message', 'message']);
/** Round bookkeeping: a step of the loop, not something a role said. */
const ROUND_MARKERS = new Set(['round.start', 'round.main.completed', 'round.review.started']);
/** A verdict's status as the agent wrote it on its last line ("…\n\ncontinue"). */
const STATUS_TAIL = /(?:^|\n)[ \t]*`?(?:continue|done|blocked|replan_requested|question|no_progress)`?[ \t.]*$/i;

const zh = (locale: Locale) => locale === 'zh-CN';
const ROLE_WORDS: Record<string, [string, string]> = {
  planner: ['规划者', 'Planner'], manager: ['经理', 'Manager'], reviewer: ['审阅者', 'Reviewer'],
  engineer: ['工程师', 'Engineer'], operator: ['你', 'you'],
};
const roleWord = (role: string, locale: Locale) => (ROLE_WORDS[role.trim().toLowerCase()] ?? [role, role])[zh(locale) ? 0 : 1];

export function isCallRow(ev: EventMsg): boolean {
  return ev.type === 'engineer.progress' && CALL_KINDS.has(String(ev.kind || ''));
}

/** Text in English where the reader reads Chinese: the host's record, not words for them. */
function foreignText(text: string, locale: Locale): boolean {
  return zh(locale) && !/[㐀-鿿]/.test(text) && /[A-Za-z]{2,}(?:\s+[A-Za-z'’,]+){3,}/.test(text);
}

/**
 * The host's own reasons for where work went (argus/engineer/round_routing.py),
 * said in the reader's language. Words a role wrote stay as written.
 */
export function plainInternalReason(text: string, locale: Locale): string {
  return text.replace(
    /Nothing changed since the ([a-z]+(?: and the [a-z]+)*) last held this work, and nothing new arrived from outside, so it went one rung up, to the ([a-z]+)\.(?: The Reviewer had handed it to the [a-z]+: )?/gi,
    (_match, passed: string, holder: string) => {
      const who = passed.split(' and the ').map(role => roleWord(role, locale)).join(zh(locale) ? '和' : ' and the ');
      if (holder.toLowerCase() === 'operator') {
        return zh(locale) ? `自${who}上次处理以来没有新变化，需要你来决定怎么继续。` : `Nothing changed since the ${who} last looked at it, so it needs your decision. `;
      }
      return zh(locale)
        ? `自${who}上次处理以来没有新变化，交给${roleWord(holder, locale)}重新安排。`
        : `Nothing changed since the ${who} last looked at it, so the ${roleWord(holder, locale)} takes it over. `;
    },
  ).trim();
}

/** The first line of a text, as a header shows it. */
function firstLine(text: string): string {
  return text.split('\n').map(line => line.trim()).find(Boolean) ?? '';
}

/** A verdict as one plain sentence — the judge's own reason — with its full record folded. */
function verdictText(phrase: string, reason: string, rest: string, locale: Locale): ProgressText {
  const said = plainInternalReason(reason, locale);
  const line = firstLine(said);
  const text = line && !foreignText(line, locale) ? line : phrase;
  const full = [phrase, said, rest].map(part => part.trim()).filter(Boolean).join('\n\n');
  return { text, technical: '', full: full !== text ? full : undefined };
}

function taskRound(ev: EventMsg, locale: Locale, title: string): string | null {
  const value = ev.round_index ?? ev.round;
  const round = typeof value === 'number' || (typeof value === 'string' && /^\d+$/.test(value)) ? Number(value) : 0;
  if (!round) return null;
  const task = title.length > 32 ? `${title.slice(0, 31)}…` : title;
  // A round counts within its task: name the task, so a new task's round 1 does
  // not read as the project starting over.
  const of = zh(locale) ? (task ? `「${task}」第 ${round} 轮` : `这项任务的第 ${round} 轮`) : (task ? `round ${round} of “${task}”` : `round ${round} of this task`);
  if (ev.type === 'round.start') return zh(locale) ? `开始${of}` : `Starting ${of}`;
  if (ev.type === 'round.main.completed') return zh(locale) ? `完成了${of}工作` : `Finished ${of}`;
  return zh(locale) ? `审阅者开始检查${of}` : `The Reviewer checks ${of}`;
}

/**
 * A row that is not a call, in plain words: round markers name their task, a
 * verdict reads as one sentence, the host's English reasons and a role's bare
 * status line are not shown raw. `taskTitle` names a task by its id.
 */
export function plainRowText(ev: EventMsg, r: RenderedLine, locale: Locale, taskTitle: (id: string) => string = () => ''): ProgressText {
  const type = String(ev.type || '');
  if (ROUND_MARKERS.has(type)) {
    const label = taskRound(ev, locale, taskTitle(String(ev.item_id || ev.mission_id || '')));
    if (label) return { text: label, technical: '' };
  }
  if (type === 'round.review.completed' && ev.review_skipped !== true) {
    // The renderer says the verdict before " · " and the reason after it.
    const phrase = r.text.split(' · ')[0] ?? r.text;
    return verdictText(phrase, String(ev.reason || ''), String(ev.next_action || '').trim() ? `${zh(locale) ? '下一步：' : 'Next: '}${String(ev.next_action).trim()}` : '', locale);
  }
  if (ev.work_kind === 'verdict' && ev.work_status !== 'planned') {
    // Earlier work as the mission view kept it: a plain status, then the reason and the next step.
    const detail = String(ev.work_detail || '');
    const [reason = '', ...rest] = detail.split(/\n[ \t]*\n/);
    return verdictText(String(ev.work_title || ''), reason, rest.join('\n\n'), locale);
  }
  const kind = String(ev.kind || '');
  const said = MESSAGE_KINDS.has(kind) ? readableRecord(r.text) : r.text;
  const statusOnly = MESSAGE_KINDS.has(kind) && STATUS_TAIL.test(said) && !said.replace(STATUS_TAIL, '').trim();
  // A bare status ending a message is the verdict, which its own row says in words.
  const cleaned = plainInternalReason(statusOnly ? said : said.replace(STATUS_TAIL, ''), locale);
  return plainDetail(cleaned, locale);
}

/**
 * What a role is doing or concluded, in one plain line: its newest message or
 * verdict, never a command. With nothing said yet, the newest tool step in
 * words, else how many commands it ran. The live card says a role with this too.
 */
export function roleHeadline(rows: Row[], locale: Locale, taskTitle?: (id: string) => string): string {
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const { ev, r } = rows[index];
    if (isCallRow(ev) || r.reasoning || ROUND_MARKERS.has(String(ev.type || ''))) continue;
    const line = firstLine(plainRowText(ev, r, locale, taskTitle).text);
    if (line) return line;
  }
  const calls = rows.filter(row => isCallRow(row.ev));
  const tool = [...calls].reverse().find(row => row.ev.kind === 'tool_use');
  if (tool) return plainToolLabel(tool.ev, locale);
  const commands = calls.filter(row => row.ev.kind === 'command_execution').length;
  if (!commands) return '';
  return zh(locale) ? `运行了 ${commands} 条命令` : `Ran ${commands} command${commands === 1 ? '' : 's'}`;
}
