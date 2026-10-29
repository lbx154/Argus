import type { EventMsg } from '../../../core/src/types';
import type { Locale } from '../i18n';
import { pairCalls, plainCommand, plainToolLabel, renderFeedRows, toolName, type FeedStep } from './feedSteps';
import { shortWorkText } from './taskLanguage';
import { eventTaskId } from './workStatus';

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

const MAX_LINES = 60;
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

/** A task's actions within its time window, oldest first; paths in `workspace` read from there. */
export function liveLines(events: EventMsg[], taskId: string, since: number, locale: Locale, until = Infinity, workspace = ''): LiveLine[] {
  const { list } = renderFeedRows(ownEvents(events, taskId, since, until), { locale, showReasoning: false });
  return pairCalls(list).map((step) => stepLine(step, locale, workspace)).filter((line): line is LiveLine => Boolean(line)).slice(-MAX_LINES);
}
