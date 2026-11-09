// PROGRESS.md: where a project's work stands, for every role and the operator to read. A projection
// of Argus's record (principle 1): rebuilt from events.jsonl when the process first needs it, kept up
// by each event after, and written by nothing else.

import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { FlowEvent, Project, Store } from './store.js';
import { NAMES } from './prompts.js';
import { isCheckout } from './worktrees.js';

type Dated = FlowEvent & { ts: string };

interface Board {
  spent: number;
  opened: Map<string, string>;
  closed: Map<string, string>;
  cost: Map<string, number>;
  /** Each role's (on each task) last letter: when, and its first line. */
  said: Map<string, { at: string; text: string }>;
}

const line = (text: string): string => {
  const first = text.split('\n').map(part => part.replace(/^#+\s*/, '').trim()).find(Boolean) ?? '';
  return first.length > 140 ? `${first.slice(0, 139)}…` : first;
};

function take(board: Board, event: Dated): void {
  const who = 'role' in event ? `${NAMES[event.role]}${'task' in event && event.task ? ` ${event.task}` : ''}` : '';
  if (event.type === 'call.end') {
    const cost = event.usage?.cost_usd ?? 0;
    board.spent += cost;
    if (event.task) board.cost.set(event.task, (board.cost.get(event.task) ?? 0) + cost);
  }
  else if (event.type === 'task.added') board.opened.set(event.task.id, event.ts);
  else if (event.type === 'task.closed') board.closed.set(event.task, event.ts);
  else if (event.type === 'handoff') board.said.set(who, { at: event.ts, text: `to ${event.to}: ${line(event.letter)}` });
  else if (event.type === 'letter') board.said.set(who, { at: event.ts, text: `to ${[...event.to, ...event.cc].join(', ')}: ${line(event.text)}` });
}

const hours = (from: string, to: string): string => {
  const minutes = Math.round((Date.parse(to) - Date.parse(from)) / 60000);
  return minutes < 90 ? `${minutes} min` : `${(minutes / 60).toFixed(1)} h`;
};

function render(project: Project, board: Board, now: string): string {
  const cell = (text: string): string => text.replace(/\|/g, '\\|');
  const row = (task: Project['tasks'][number]): string => {
    const opened = board.opened.get(task.id);
    const closed = board.closed.get(task.id);
    const state = task.status === 'open' ? `open, with the ${NAMES[task.holder]}` : 'closed';
    return `| ${task.id} | ${cell(line(task.title))} | ${state} | ${opened ? hours(opened, closed ?? now) : ''} | $${(board.cost.get(task.id) ?? 0).toFixed(1)} | ${cell(task.note ? line(task.note.text) : '')} |`;
  };
  const open = project.tasks.filter(task => task.status === 'open');
  const closed = project.tasks.filter(task => task.status === 'closed').reverse().slice(0, 20);
  const said = [...board.said].sort(([, a], [, b]) => b.at.localeCompare(a.at)).slice(0, 20)
    .map(([who, { at, text }]) => `- ${at.slice(0, 16).replace('T', ' ')} ${who}, ${cell(text)}`);
  let commits = '';
  if (isCheckout(project.workdir)) {
    try {
      commits = execFileSync('git', ['-C', project.workdir, 'log', '-12', '--format=- %h %an: %s'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    } catch { /* no commit yet */ }
  }
  return [
    '# Where the work stands',
    '',
    `Rebuilt by Argus from its record at ${now.slice(0, 16).replace('T', ' ')} UTC; nobody edits it.`,
    '',
    // A request may open with a label ("Short goal:"); the first line that says something stands for it.
    `Request: ${line(project.request.split('\n').find(text => text.trim() && !text.trim().endsWith(':')) ?? '')}`,
    `Spent: $${board.spent.toFixed(1)}`,
    '',
    '## Tasks',
    '',
    '| Task | Title | State | Ran | Cost | Last word |',
    '| --- | --- | --- | --- | --- | --- |',
    ...[...open, ...closed].map(row),
    '',
    '## Latest from each role',
    '',
    ...said,
    ...commits ? ['', '## Latest commits', '', commits] : [],
    '',
  ].join('\n');
}

export class Progress {
  private readonly boards = new Map<string, Board>();

  constructor(private readonly store: Store) {}

  /** Takes an event just recorded and rewrites the project's PROGRESS.md. */
  note(project: Project, event: FlowEvent): void {
    const now = new Date().toISOString();
    let board = this.boards.get(project.id);
    if (board) take(board, { ts: now, ...event } as Dated);
    else this.boards.set(project.id, board = this.read(project.id));
    if (event.type !== 'call.start') writeFileSync(join(this.store.dir(project.id), 'PROGRESS.md'), render(project, board, now));
  }

  /** The record so far, this event included; a step changes nothing here, and a line cut short is skipped. */
  private read(id: string): Board {
    const board: Board = { spent: 0, opened: new Map(), closed: new Map(), cost: new Map(), said: new Map() };
    let text = '';
    try { text = readFileSync(join(this.store.dir(id), 'events.jsonl'), 'utf8'); } catch { return board; }
    for (const entry of text.split('\n')) {
      if (!entry || entry.includes('"type":"step"')) continue;
      try { take(board, JSON.parse(entry) as Dated); } catch { /* a line cut short */ }
    }
    return board;
  }
}
