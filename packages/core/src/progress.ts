// PROGRESS.md: where a project's work stands, for every role and the operator to read. A projection
// of Argus's record (principle 1): rebuilt from events.jsonl when the process first needs it, kept up
// by each event after, and written by nothing else.

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { lstat, readdir } from 'node:fs/promises';
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
    total: 'In all', left: 'Worktrees closed tasks left', leftWhy: 'Each is a full copy of the workspace that its task did not delete. Its task is closed, so it stays on the disk until someone deletes it (`git worktree remove <folder>`, run in the workspace).',
    roles: { manager: 'Manager', planner: 'Planner', engineer: 'Engineer', reviewer: 'Reviewer', operator: 'operator' } },
  zh: { title: '工作进展', rebuilt: (at: string) => `Argus 根据记录于 ${at} UTC 重建，没有人手工编辑。`, request: '请求', spent: '已花费',
    tasks: '任务', head: '| 任务 | 标题 | 状态 | 已跑 | 花费 | 最后一句 |', open: (holder: string) => `进行中，在${holder}手里`, closed: '已关闭',
    said: '各角色最近说的话', commits: '最近的提交', to: '给', min: '分钟', h: '小时',
    total: '合计', left: '已关闭任务留下的工作树', leftWhy: '每个都是工作区的一份完整拷贝，任务没有删。任务已经关闭，所以它会一直占着磁盘，直到有人删掉它（在工作区里运行 `git worktree remove <目录>`）。',
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

/** The worktrees of closed tasks that are still on the disk: their tasks can no longer delete them. */
const leftBy = (project: Project): { id: string; dir: string }[] => project.tasks.flatMap(task =>
  task.status === 'closed' && typeof task.place === 'object' && existsSync(task.place.dir) ? [{ id: task.id, dir: task.place.dir }] : []);

/** What a folder holds on the disk, in bytes, read without holding up the work; what cannot be read counts nothing. */
async function sizeOf(dir: string): Promise<number> {
  let bytes = 0;
  for (const folders = [dir]; folders.length;) {
    const folder = folders.pop()!;
    for (const entry of await readdir(folder, { withFileTypes: true }).catch(() => [])) {
      const path = join(folder, entry.name);
      if (entry.isDirectory()) folders.push(path);
      else bytes += await lstat(path).then(stat => stat.blocks ? stat.blocks * 512 : stat.size, () => 0);
    }
  }
  return bytes;
}

const size = (bytes: number): string => bytes >= 2 ** 30 ? `${(bytes / 2 ** 30).toFixed(1)} GiB` : `${Math.max(1, Math.round(bytes / 2 ** 20))} MiB`;

function render(project: Project, board: Board, now: string, sizes: ReadonlyMap<string, number>): string {
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
  const left = leftBy(project);
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
    ...left.length ? ['', `## ${words.left}`, '', words.leftWhy, '', ...left.map(({ id, dir }) => `- ${id}: ${sizes.has(dir) ? `${size(sizes.get(dir)!)}, ` : ''}${dir}`),
      ...left.length > 1 && left.every(({ dir }) => sizes.has(dir)) ? ['', `${words.total}: ${size(left.reduce((sum, { dir }) => sum + sizes.get(dir)!, 0))}`] : []] : [],
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
  /** How much each worktree a closed task left holds, measured once in this process. */
  private readonly sizes = new Map<string, number>();
  private readonly measuring = new Map<string, Promise<void>>();

  constructor(private readonly store: Store) {}

  /** Takes an event just recorded and rewrites the project's PROGRESS.md. */
  note(project: Project, event: FlowEvent): void {
    const now = new Date().toISOString();
    let board = this.boards.get(project.id);
    if (board) take(board, { ts: now, ...event } as Dated);
    else this.boards.set(project.id, board = this.read(project));
    if (event.type === 'call.start') return;
    const write = (): void => writeFileSync(join(this.store.dir(project.id), 'PROGRESS.md'), render(project, board, new Date().toISOString(), this.sizes));
    write();
    // A worktree's size is read beside the work, and the page written again once it is known.
    for (const { dir } of leftBy(project)) {
      if (this.sizes.has(dir) || this.measuring.has(dir)) continue;
      this.measuring.set(dir, sizeOf(dir).then(bytes => {
        this.sizes.set(dir, bytes);
        this.measuring.delete(dir);
        try { write(); } catch { /* the project is gone: nothing reads its page */ }
      }));
    }
  }

  /** Resolves once every size being read is on the page. */
  async measured(): Promise<void> {
    while (this.measuring.size) await Promise.all(this.measuring.values());
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
