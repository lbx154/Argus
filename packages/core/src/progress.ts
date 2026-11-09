// PROGRESS.md: where a project's work stands, for every role and the operator to read. A projection
// of Argus's record (principle 1): rebuilt from events.jsonl when the process first needs it, kept up
// by each event after, and written by nothing else.

import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { FlowEvent, Project, Store } from './store.js';
import { isCheckout } from './worktrees.js';

type Dated = FlowEvent & { ts: string };

interface Board {
  spent: number;
  opened: Map<string, string>;
  closed: Map<string, string>;
  cost: Map<string, number>;
  /** Each role's (on each task) last letter: when, and its first line. */
  said: Map<string, { at: string; to: string[]; text: string }>;
  /** The operator's language, as their latest words are written: the page is in it. */
  zh: boolean;
}

const CJK = /[\u3400-\u9fff]/;

/** The page's own words, in the operator's language; what the roles wrote stays as they wrote it. */
const WORDS = {
  en: { title: 'Where the work stands', rebuilt: (at: string) => `Rebuilt by Argus from its record at ${at} UTC; nobody edits it.`, request: 'Request', spent: 'Spent',
    tasks: 'Tasks', head: '| Task | Title | State | Ran | Cost | Last word |', open: (holder: string) => `open, with the ${holder}`, closed: 'closed',
    said: 'Latest from each role', commits: 'Latest commits', to: 'to', min: 'min', h: 'h',
    roles: { manager: 'Manager', planner: 'Planner', engineer: 'Engineer', reviewer: 'Reviewer', operator: 'operator' } },
  zh: { title: '工作进展', rebuilt: (at: string) => `Argus 根据记录于 ${at} UTC 重建，没有人手工编辑。`, request: '请求', spent: '已花费',
    tasks: '任务', head: '| 任务 | 标题 | 状态 | 已跑 | 花费 | 最后一句 |', open: (holder: string) => `进行中，在${holder}手里`, closed: '已关闭',
    said: '各角色最近说的话', commits: '最近的提交', to: '给', min: '分钟', h: '小时',
    roles: { manager: '经理', planner: '规划者', engineer: '工程师', reviewer: '审查员', operator: '操作者' } },
} as const;

const line = (text: string): string => {
  const first = text.split('\n').map(part => part.replace(/^#+\s*/, '').trim()).find(Boolean) ?? '';
  return first.length > 140 ? `${first.slice(0, 139)}…` : first;
};

function take(board: Board, event: Dated): void {
  const who = 'role' in event ? `${event.role}${'task' in event && event.task ? ` ${event.task}` : ''}` : '';
  if (event.type === 'message' && event.from === 'operator') board.zh = CJK.test(event.text);
  if (event.type === 'call.end') {
    const cost = event.usage?.cost_usd ?? 0;
    board.spent += cost;
    if (event.task) board.cost.set(event.task, (board.cost.get(event.task) ?? 0) + cost);
  }
  else if (event.type === 'task.added') board.opened.set(event.task.id, event.ts);
  else if (event.type === 'task.closed') board.closed.set(event.task, event.ts);
  else if (event.type === 'handoff') board.said.set(who, { at: event.ts, to: [event.to], text: line(event.letter) });
  else if (event.type === 'letter') board.said.set(who, { at: event.ts, to: [...event.to, ...event.cc], text: line(event.text) });
}

const hours = (from: string, to: string, words: typeof WORDS[keyof typeof WORDS]): string => {
  const minutes = Math.round((Date.parse(to) - Date.parse(from)) / 60000);
  return minutes < 90 ? `${minutes} ${words.min}` : `${(minutes / 60).toFixed(1)} ${words.h}`;
};

function render(project: Project, board: Board, now: string): string {
  const words = WORDS[board.zh ? 'zh' : 'en'];
  const name = (who: string): string => { const [role, task] = who.split(' '); return `${words.roles[role as keyof typeof words.roles] ?? role}${task ? ` ${task}` : ''}`; };
  const cell = (text: string): string => text.replace(/\|/g, '\\|');
  const row = (task: Project['tasks'][number]): string => {
    const opened = board.opened.get(task.id);
    const closed = board.closed.get(task.id);
    const state = task.status === 'open' ? words.open(words.roles[task.holder]) : words.closed;
    return `| ${task.id} | ${cell(line(task.title))} | ${state} | ${opened ? hours(opened, closed ?? now, words) : ''} | $${(board.cost.get(task.id) ?? 0).toFixed(1)} | ${cell(task.note ? line(task.note.text) : '')} |`;
  };
  const open = project.tasks.filter(task => task.status === 'open');
  const closed = project.tasks.filter(task => task.status === 'closed').reverse().slice(0, 20);
  const said = [...board.said].sort(([, a], [, b]) => b.at.localeCompare(a.at)).slice(0, 20)
    .map(([who, { at, to, text }]) => `- ${at.slice(0, 16).replace('T', ' ')} ${name(who)}, ${words.to} ${to.map(name).join(', ')}: ${cell(text)}`);
  let commits = '';
  if (isCheckout(project.workdir)) {
    try {
      commits = execFileSync('git', ['-C', project.workdir, 'log', '-12', '--format=- %h %an: %s'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    } catch { /* no commit yet */ }
  }
  return [
    `# ${words.title}`,
    '',
    words.rebuilt(now.slice(0, 16).replace('T', ' ')),
    '',
    // A request may open with a label ("Short goal:"); the first line that says something stands for it.
    `${words.request}: ${line(project.request.split('\n').find(text => text.trim() && !text.trim().endsWith(':')) ?? '')}`,
    `${words.spent}: $${board.spent.toFixed(1)}`,
    '',
    `## ${words.tasks}`,
    '',
    words.head,
    '| --- | --- | --- | --- | --- | --- |',
    ...[...open, ...closed].map(row),
    '',
    `## ${words.said}`,
    '',
    ...said,
    ...commits ? ['', `## ${words.commits}`, '', commits] : [],
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
    else this.boards.set(project.id, board = this.read(project));
    if (event.type !== 'call.start') writeFileSync(join(this.store.dir(project.id), 'PROGRESS.md'), render(project, board, now));
  }

  /** The record so far, this event included; a step changes nothing here, and a line cut short is skipped. */
  private read({ id, request }: Project): Board {
    const board: Board = { spent: 0, opened: new Map(), closed: new Map(), cost: new Map(), said: new Map(), zh: CJK.test(request) };
    let text = '';
    try { text = readFileSync(join(this.store.dir(id), 'events.jsonl'), 'utf8'); } catch { return board; }
    for (const entry of text.split('\n')) {
      if (!entry || entry.includes('"type":"step"')) continue;
      try { take(board, JSON.parse(entry) as Dated); } catch { /* a line cut short */ }
    }
    return board;
  }
}
