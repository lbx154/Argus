import type { EventMsg, MissionView, Snapshot } from '../../../core/src/types';
import type { Locale } from '../i18n';
import { pairCalls, plainCommand, plainToolLabel, renderFeedRows, toolName, type FeedStep } from './feedSteps';
import { shortWorkText } from './taskLanguage';
import { currentWorkStartedAt, currentWorkStatus, eventTaskId, type WorkStatus } from './workStatus';

/** One thing the team did, as a person would say it. */
export interface LiveLine {
  key: string;
  ts: number;
  role: string;
  kind: 'command' | 'tool' | 'edit' | 'message' | 'milestone';
  text: string;
  /** A call that has started and not reported back yet. */
  running: boolean;
  failed: boolean;
}

export interface LiveActivity {
  /** Nothing to show: not running, or the conversation itself already shows the work. */
  show: boolean;
  tone: 'working' | 'waiting' | 'quiet';
  role: string;
  headline: string;
  taskTitle: string;
  position: { index: number; total: number } | null;
  startedAt: number | null;
  lastAt: number | null;
  /** Why it is not visibly working right now, when that is the case. */
  note: string;
  lines: LiveLine[];
}

/** Past this, a working step with no new action says so instead of looking busy. */
export const QUIET_AFTER_S = 10 * 60;
const MAX_LINES = 60;
const HIDDEN_STATUS = new Set(['skipped', 'cancelled', 'canceled', 'superseded']);
const WORK_EVENT = /^(?:engineer\.progress|round\.|life\.(?:mission|phase|planner|research)\.|advisor\.consultation\.)/;

const field = (event: EventMsg, key: string) => String((event as Record<string, unknown>)[key] ?? '');
const clip = (text: string, n: number) => (text.length <= n ? text : `${text.slice(0, n - 1).trimEnd()}…`);

function stepLine(step: FeedStep, locale: Locale, workspace: string): LiveLine | null {
  const zh = locale === 'zh-CN';
  const event = step.ev;
  const ts = Number(step.latest.ts || event.ts || 0);
  const role = step.r.role || 'engineer';
  const base = { key: step.key, ts, role, running: step.status === 'running', failed: step.status === 'failed' || step.r.tone === 'err' };
  if (field(event, 'type') === 'engineer.progress') {
    const kind = field(event, 'kind');
    if (kind === 'command_execution') {
      const command = clip(plainCommand(field(event, 'text') || field(event, 'command') || field(event, 'action_summary'), workspace), 120);
      return command ? { ...base, kind: 'command', text: `${zh ? '运行' : 'Run'} ${command}` } : null;
    }
    if (kind === 'tool_use' || kind === 'file_change') {
      const label = plainToolLabel(event, locale);
      return { ...base, kind: kind === 'file_change' || /edit|write|patch/i.test(toolName(event)) ? 'edit' : 'tool', text: label };
    }
    if (kind === 'tool_result') return null;
    const text = step.r.text.split('\n').map((part) => part.trim()).find(Boolean) ?? '';
    return text ? { ...base, kind: 'message', text: shortWorkText(text, 160) } : null;
  }
  const text = step.r.text.split('\n').map((part) => part.trim()).find(Boolean) ?? '';
  return text ? { ...base, kind: 'milestone', text: shortWorkText(text, 140) } : null;
}

/**
 * A task's events within its time window. Progress events do not always name
 * their task, so an unnamed one belongs to the task running at that moment,
 * as the mission view attributes it.
 */
function ownEvents(events: EventMsg[], taskId: string, since: number, until = Infinity): EventMsg[] {
  return events.filter((event) => {
    const ts = Number(event.ts || 0);
    if (ts < since || ts > until) return false;
    // Work only: conversation turns and service records are not the team acting.
    if (!WORK_EVENT.test(String(event.type || ''))) return false;
    if (String(event.run_label || '').startsWith('map-summary') || event.turn_step === true) return false;
    const id = eventTaskId(event);
    return !taskId || !id || id === taskId;
  });
}

function linesFrom(own: EventMsg[], locale: Locale, workspace: string): LiveLine[] {
  const { list } = renderFeedRows(own, { locale, showReasoning: false });
  return pairCalls(list).map((step) => stepLine(step, locale, workspace)).filter((line): line is LiveLine => Boolean(line)).slice(-MAX_LINES);
}

/** A task's actions within its time window, oldest first; paths in `workspace` read from there. */
export function liveLines(events: EventMsg[], taskId: string, since: number, locale: Locale, until = Infinity, workspace = ''): LiveLine[] {
  return linesFrom(ownEvents(events, taskId, since, until), locale, workspace);
}

function headlineFor(status: WorkStatus, zh: boolean, title: string): string {
  const task = shortWorkText(title, 60);
  switch (status.role) {
    case 'planner': return zh ? '正在安排下一步' : 'Planning the next step';
    case 'reviewer': return task ? (zh ? `正在审核：${task}` : `Reviewing: ${task}`) : (zh ? '正在审核结果' : 'Reviewing the result');
    case 'manager': return zh ? '正在整理进展' : 'Summarizing progress';
    default: return task ? (zh ? `正在执行：${task}` : `Working on: ${task}`) : (zh ? '正在准备下一步' : 'Getting the next step ready');
  }
}

function minutes(seconds: number, zh: boolean): string {
  const value = Math.max(1, Math.floor(seconds / 60));
  if (value < 60) return zh ? `${value} 分钟` : `${value} min`;
  const hours = Math.floor(value / 60);
  return zh ? `${hours} 小时 ${value % 60} 分钟` : `${hours} h ${value % 60} min`;
}

/**
 * What the project is doing right now, for the card above the composer:
 * the step, who is on it, its most recent actions, and — when it is not
 * visibly moving — why, said plainly rather than shown as busy.
 */
export function liveActivity(
  snapshot: Snapshot | undefined,
  view: MissionView | null | undefined,
  events: EventMsg[],
  nowSeconds: number,
  locale: Locale,
): LiveActivity {
  const zh = locale === 'zh-CN';
  const status = currentWorkStatus(snapshot, view, events, nowSeconds);
  const empty: LiveActivity = {
    show: false, tone: 'working', role: '', headline: '', taskTitle: '', position: null,
    startedAt: null, lastAt: null, note: '', lines: [],
  };
  // The Manager's own reply streams into the conversation with its steps.
  if (!snapshot?.daemon.alive || status.foreground) return empty;
  if (!['running', 'waiting', 'unknown'].includes(status.state)) return empty;
  const taskId = status.taskId;
  const started = taskId ? currentWorkStartedAt(snapshot, view, taskId) : Date.parse(snapshot.daemon.started_at_iso || '') / 1000 || 0;
  const own = ownEvents(events, taskId, started);
  // Hidden events (reasoning, tool output) still show the step is moving.
  const lastAt = Math.max(0, ...own.map((event) => Number(event.ts || 0))) || null;
  // Some agents never echo a successful call, so "still running" holds only
  // for the very last action, and only while nothing at all came after it.
  const lines = linesFrom(own, locale, snapshot.session.workdir || snapshot.session.cwd).map((line, index, all) => ({
    ...line, running: line.running && index === all.length - 1 && (lastAt ?? 0) <= line.ts + 1,
  }));
  const lastLine = lines.at(-1);
  const plan = (snapshot.backlog ?? []).filter((item) => !HIDDEN_STATUS.has(String(item.status)));
  const index = taskId ? plan.findIndex((item) => item.id === taskId) : -1;
  const quietFor = lastAt === null ? null : nowSeconds - lastAt;
  let tone: LiveActivity['tone'] = status.state === 'running' ? 'working' : 'waiting';
  let note = '';
  if (lastLine?.running && quietFor !== null) {
    note = zh ? `命令仍在运行，已运行 ${minutes(nowSeconds - lastLine.ts, zh)}` : `The command is still running (${minutes(nowSeconds - lastLine.ts, zh)})`;
  } else if (quietFor !== null && quietFor > QUIET_AFTER_S) {
    tone = 'quiet';
    note = zh ? `${minutes(quietFor, zh)}没有新动作，可能在等命令或模型返回。` : `No new action for ${minutes(quietFor, zh)}; probably waiting on a command or the model.`;
  }
  return {
    show: true,
    tone,
    role: status.role,
    headline: headlineFor(status, zh, status.title),
    taskTitle: status.title,
    position: index >= 0 ? { index: index + 1, total: plan.length } : null,
    startedAt: started > 0 ? started : null,
    lastAt,
    note,
    lines,
  };
}

/** "12:34" / "1:02:03" for a running step. */
export function clockLabel(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (value: number) => String(value).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}
