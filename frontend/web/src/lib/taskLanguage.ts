import type { EventMsg } from '../../../core/src/types';
import { plainCommand } from './feedSteps';

// Markup that would read as stray marks once a text is cut to a line; the same
// as sanitize_text in argus/core/residual_risk.py.
const MARKUP = /(?:^|\s)(?:#{1,6}|>|-{3,}|={3,})(?=\s)|`+|\*\*/g;

/** User-facing actions; role identifiers belong in the work details. The
 * markup goes before the cut, so no cut keeps half of a pair of marks. */
export function shortWorkText(text: string, max = 120): string {
  const line = text.replace(MARKUP, '').replace(/\s+/g, ' ').trim();
  return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
}

export function roleActionLabel(role: string | undefined, zh: boolean, title = '', message = ''): string {
  const task = shortWorkText(title, 70);
  switch (role) {
    case 'manager': return shortWorkText(message) || (zh ? '正在回复你的消息' : 'Replying to your message');
    case 'planner': return zh ? '正在安排接下来要做的事' : 'Planning what to do next';
    case 'reviewer': return task
      ? zh ? `正在检查结果：${task}` : `Checking the result: ${task}`
      : zh ? '正在检查结果是否正确' : 'Checking whether the result is correct';
    default: return task
      ? zh ? `正在处理：${task}` : `Working on: ${task}`
      : zh ? '正在处理当前任务' : 'Working on the current task';
  }
}


/** The current Manager call's words, from the independent role record. */
export function latestManagerMessage(events: readonly EventMsg[]): string {
  for (let index = events.length - 1; index >= 0; index--) {
    const event = events[index];
    if (event.agent_layer !== 'manager') continue;
    if (event.kind === 'agent_message' && typeof event.text === 'string' && event.text.trim()) return event.text;
    if (event.type === 'provider.request.started') break;
  }
  return '';
}

/** A thought as a status line says it: its summary's heading, as codex's screen shows it. */
export function thoughtHeading(text: string, zh: boolean): string {
  const heading = shortWorkText(text.trim().split('\n')[0]!);
  return zh && !/\p{Script=Han}/u.test(heading) ? '' : heading;
}

/** What the current Manager call is at since it last said anything, as codex's status line says
 * it: the heading of its latest thought, as its backend sums it up, or the command it runs. */
export function managerDoing(events: readonly EventMsg[], zh: boolean): string {
  for (let index = events.length - 1; index >= 0; index--) {
    const event = events[index];
    if (event.agent_layer !== 'manager') continue;
    if (event.type === 'provider.request.started' || event.kind === 'agent_message') break;
    const text = typeof event.text === 'string' ? event.text.trim() : '';
    if (event.kind === 'reasoning' && text) return thoughtHeading(text, zh);
    if (event.kind === 'command_execution' && text) return plainCommand(text);
  }
  return '';
}

/** Attribution is its own Markdown block so the question can start with a table. */
export function questionSentence(role: string, task: string | undefined, question: string, zh: boolean): string {
  return zh ? `${role}${task ? `在「${task}」上` : ''}问：\n\n${question}`
    : `${role} asks${task ? ` about “${task}”` : ''}:\n\n${question}`;
}
