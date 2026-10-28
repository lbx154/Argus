// Each role's prompt: its rules (prompts/<role>.md and prompts/common.md) first, the same for
// every turn, then what this turn is about. A resumed session already holds the rules, so it is
// sent only what is new. What each hand-off field means is in the form's own schema, not here.

import { readFileSync } from 'node:fs';
import type { Role } from './types.js';
import type { Note, Project, Task, Ticket } from './store.js';

const NAMES: Record<Note['from'], string> = {
  manager: 'Manager', planner: 'Planner', engineer: 'Engineer', reviewer: 'Reviewer', operator: 'operator',
};

const rules = (role: Role): string =>
  [role, 'common'].map(name => readFileSync(new URL(`../prompts/${name}.md`, import.meta.url), 'utf8').trim()).join('\n\n');
const section = (title: string, body: string): string => `## ${title}\n${body}`;
const join = (...parts: (string | false | null)[]): string => parts.filter(Boolean).join('\n\n');
const bullets = (title: string, items: string[]): string => items.length ? `${title}:\n${items.map(item => `- ${item}`).join('\n')}` : '';

export function ticketText({ goal, acceptance, constraints, context }: Ticket): string {
  return join(goal, bullets('Acceptance', acceptance), bullets('Constraints', constraints), bullets('Read first', context));
}

export function managerPrompt(project: Project, record: string, message: string, resumed: boolean): string {
  if (resumed) return message;
  return join(rules('manager'), section('Where things are', [
    `Workspace: ${project.workdir}`,
    `Argus's record of this project: ${record} (project.json holds the ticket, the tasks and their state; events.jsonl what happened)`,
    `Today is ${new Date().toISOString().slice(0, 10)} (UTC). Your training knowledge may predate it: call nothing the latest unless a source checked now says so.`,
  ].join('\n')), section('Message', message));
}

/** The Engineer's or Reviewer's turn on `task`, whichever holds it. */
export function taskPrompt(project: Project, task: Task, resumed: boolean): string {
  const note = task.note && section(`From the ${NAMES[task.note.from]}`, task.note.text);
  if (resumed) {
    const again = task.holder === 'reviewer' && task.note?.from === 'engineer'
      && 'The Engineer has worked on the task since your last judgment. Judge the current files afresh: your earlier judgment is not a fact.';
    return join(again, note || 'Continue the task.');
  }
  return join(rules(task.holder), project.ticket && section('Ticket', ticketText(project.ticket)),
    task.objective && section(`Task ${task.id}: ${task.title}`, task.objective), note);
}

export function plannerPrompt(project: Project, resumed: boolean): string {
  const tasks = project.tasks.map(task => `- ${task.id} [${[
    task.status === 'open' ? `open, with the ${NAMES[task.holder]}` : task.status,
    task.deps.length && `after ${task.deps.join(', ')}`,
  ].filter(Boolean).join(', ')}] ${task.title}`);
  return join(!resumed && rules('planner'), project.ticket && section('Ticket', ticketText(project.ticket)),
    section('Tasks', tasks.join('\n') || '(none yet)'), section('Since your last turn', project.inbox.join('\n\n')));
}

/** What a role that hands the work to the Planner or Manager tells it. */
export function handedTo(role: Role, task: Task | null, summary: string, note: string): string {
  return join(`The ${NAMES[role]} handed ${task ? `task ${task.id} (${task.title})` : 'the work'} to you: ${summary}`, note);
}

export function answered(question: string, answer: string): string {
  return `You asked the operator: ${question}\nThey answered: ${answer}`;
}
