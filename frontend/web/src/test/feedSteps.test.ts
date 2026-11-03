import { roleSnapshot } from './serverSnapshot';
import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { EventMsg, Snapshot } from '../api';
import {
  MIN_GROUP_SIZE,
  foldFeedRows,
  groupSummary,
  renderFeedRows,
  stepTarget,
  readableToolProgress,
  type FeedGroup,
  type FeedStep,
} from '../lib/feedSteps';
import { translate } from '../i18n';
import { EventStream } from './serverSnapshot';


const rows = (events: EventMsg[]) => renderFeedRows(events, { locale: 'en', showReasoning: true }).list;
const fold = (events: EventMsg[]) => foldFeedRows(rows(events), 'en');
const en = (key: string, variables?: Record<string, string | number>) => translate(key, variables, 'en');
const zh = (key: string, variables?: Record<string, string | number>) => translate(key, variables, 'zh-CN');

it('describes a file operation without copying its content into the status line', () => {
  const event = { type: 'engineer.progress', kind: 'tool_use', text: 'write: {"path":"research/notes.md","content":"large private work body"}' };
  expect(readableToolProgress(event, 'zh-CN')).toEqual({ title: '更新项目文件', detail: 'notes.md' });
  expect(event.text).toContain('large private work body');
  expect(readableToolProgress({ type: 'engineer.progress', kind: 'agent_message', text: event.text }, 'zh-CN')).toBeNull();
});

it('summarizes a multiline command without placing its source in the status line', () => {
  const event = { type: 'engineer.progress', kind: 'command_execution', text: 'python - <<\'PY\'\nprint("command body")\nPY' };
  expect(readableToolProgress(event, 'zh-CN')).toEqual({ title: '正在运行命令', detail: '' });
  expect(readableToolProgress(event, 'en')).toEqual({ title: 'Running a command', detail: '' });
  expect(event.text).toContain('command body');
});

let clock = 0;
const call = (tool: string, args: Record<string, unknown>, extra: Record<string, unknown> = {}): EventMsg => ({
  type: 'engineer.progress',
  kind: 'tool_use',
  text: `${tool}: ${JSON.stringify(args)}`,
  tool_name: tool,
  agent_layer: 'engineer',
  status: 'running',
  ts: (clock += 1),
  ...extra,
} as EventMsg);
const shell = (command: string): EventMsg => ({
  type: 'engineer.progress',
  kind: 'command_execution',
  text: command,
  tool_name: 'bash',
  agent_layer: 'engineer',
  status: 'running',
  ts: (clock += 1),
} as EventMsg);
const said = (text: string, extra: Record<string, unknown> = {}): EventMsg => ({
  type: 'engineer.progress',
  kind: 'agent_message',
  text,
  agent_layer: 'engineer',
  ts: (clock += 1),
  ...extra,
} as EventMsg);

describe('foldFeedRows · tool groups (rule a, b)', () => {
  it('folds three or more consecutive calls of one tool into one row with a summary and its members', () => {
    const files = ['a.py', 'b.py', 'c.py', 'd.py', 'e.py'];
    const folded = fold(files.map((name) => call('view', { path: `/repo/src/${name}` })));
    expect(folded).toHaveLength(1);
    const group = folded[0] as FeedGroup;
    expect(group.kind).toBe('group');
    expect(group.steps).toHaveLength(5);
    expect(group.action).toBe('read');
    expect(group.targets).toEqual(files);
    expect(groupSummary(group, en, 'en')).toBe('Read 5 files: a.py, b.py, c.py…');
    expect(groupSummary(group, zh, 'zh-CN')).toBe('读取了 5 个文件：a.py、b.py、c.py…');
  });

  it('leaves two reads as two lines — the threshold is three', () => {
    expect(MIN_GROUP_SIZE).toBe(3);
    const folded = fold([call('view', { path: 'a.py' }), call('view', { path: 'b.py' })]);
    expect(folded.map((row) => row.kind)).toEqual(['step', 'step']);
  });

  it('folds no run across two tasks a role works on side by side', () => {
    const folded = fold(['a.py', 'b.py', 'c.py', 'd.py'].map((path, index) => call('view', { path }, { item_id: index % 2 ? 't2' : 't1' })));
    expect(folded.map((row) => row.kind)).toEqual(['step', 'step', 'step', 'step']);
  });

  it('breaks a run on a different tool or a message, and folds each tool on its own', () => {
    const folded = fold([
      call('view', { path: 'a.py' }),
      call('view', { path: 'b.py' }),
      call('view', { path: 'c.py' }),
      said('Now searching.'),
      call('rg', { pattern: 'alpha' }),
      call('rg', { pattern: 'beta' }),
      call('rg', { pattern: 'gamma' }),
      call('rg', { pattern: 'delta' }),
      call('view', { path: 'd.py' }),
    ]);
    expect(folded.map((row) => (row.kind === 'group' ? `${row.action}×${row.steps.length}` : 'step'))).toEqual([
      'read×3', 'step', 'search×4', 'step',
    ]);
    const search = folded[2] as FeedGroup;
    expect(groupSummary(search, en, 'en')).toBe('Searched 4 times: alpha, beta, gamma…');
  });

  it('never folds shell commands, however many run in a row', () => {
    const folded = fold(['pytest -q', 'ruff check .', 'git status', 'ls -la', 'make'].map(shell));
    expect(folded).toHaveLength(5);
    expect(folded.every((row) => row.kind === 'step')).toBe(true);
  });

  it('folds consecutive file changes by kind and names the files', () => {
    const change = (path: string): EventMsg => ({
      type: 'engineer.progress', kind: 'file_change', text: `*** Begin Patch\n*** Update File: ${path}\n+x`, agent_layer: 'engineer', ts: (clock += 1),
    } as EventMsg);
    const folded = fold([change('src/a.py'), change('src/b.py'), change('docs/c.md')]);
    expect(folded).toHaveLength(1);
    expect(groupSummary(folded[0] as FeedGroup, en, 'en')).toBe('Edited 3 files: a.py, b.py, c.md');
  });

  it('names web pages by their address and counts failures inside a group', () => {
    const folded = fold([
      call('web_fetch', { url: 'https://arxiv.org/abs/1706.03762' }),
      call('web_fetch', { url: 'https://openreview.net/forum?id=abc' }),
      call('web_fetch', { url: 'https://example.org/paper' }),
      call('web_fetch', { url: 'https://example.org/paper' }, { status: 'failed' }),
    ]);
    expect(folded).toHaveLength(1);
    const group = folded[0] as FeedGroup;
    expect(group.failed).toBe(1);
    expect(groupSummary(group, en, 'en')).toBe('Looked up 3 web pages: arxiv.org/abs/1706.03762, openreview.net/forum?id=abc, example.org/paper · 1 failed');
  });

  it('finds the target even when the emitter cut the arguments short', () => {
    const cut = call('view', {});
    cut.text = 'view: {"path": "/very/long/path/to/notes.md", "view_range": [1, -1], "forceReadLarg';
    const [step] = fold([cut]) as FeedStep[];
    expect(stepTarget(step, 'en')).toBe('notes.md');
  });
});

describe('foldFeedRows · a call and its outcome (rule c)', () => {
  it('folds the failed echo of a call into the call, even with other calls between them', () => {
    const folded = fold([
      call('rg', { pattern: 'one' }),
      call('view', { path: 'x.py' }),
      call('rg', { pattern: 'one' }, { status: 'failed', ts: 99 }),
    ]);
    expect(folded).toHaveLength(2);
    const first = folded[0] as FeedStep;
    expect(first.status).toBe('failed');
    expect(first.latest.ts).toBe(99);
    expect(first.repeat).toBe(1);
  });

  it('attaches a tool_result row to its call by call_id, and keeps an orphan result as a row', () => {
    const folded = fold([
      call('view', { path: 'x.py' }, { call_id: 'c1' }),
      { type: 'engineer.progress', kind: 'tool_result', text: '1 README.md\n2 src', call_id: 'c1', tool_name: 'view', status: 'completed', agent_layer: 'engineer', ts: 50 } as EventMsg,
      { type: 'engineer.progress', kind: 'tool_result', text: 'nobody asked for this', call_id: 'ghost', tool_name: 'view', agent_layer: 'engineer', ts: 51 } as EventMsg,
    ]);
    expect(folded).toHaveLength(2);
    const paired = folded[0] as FeedStep;
    expect(paired.status).toBe('completed');
    expect(paired.result?.r.text).toContain('README.md');
    expect((folded[1] as FeedStep).r.text).toContain('nobody asked');
  });
});

describe('foldFeedRows · repeated sentences and failure streaks (rules d, e)', () => {
  it('keeps streaming fragments as one growing row, then folds on the merged result', () => {
    const folded = fold([
      said('Checking the', { message_id: 'm1', replace: true }),
      said('Checking the sources now.', { message_id: 'm1', replace: true }),
      said('Checking the sources now.', { message_id: 'm2' }),
    ]);
    expect(folded).toHaveLength(1);
    const step = folded[0] as FeedStep;
    expect(step.r.text).toBe('Checking the sources now.');
    expect(step.repeat).toBe(2);
  });
});

describe('EventStream renders folded rows', () => {
  it("folds the role's own words and commands until opened", () => {
    // Trial s-f0e1b2f0: the Engineer's lane read "**尚未实现：取整规则存在歧义。** `sizefmt/core.py` 中…".
    const html = renderToStaticMarkup(createElement(EventStream, {
      events: [], snapshot: roleSnapshot([shell('ls sizefmt/__init__.py'), said('**尚未实现：取整规则存在歧义。** `sizefmt/core.py` 中，`format_kib` 舍弃小数。')]),
      connected: true,
      showReasoning: false,
    }));

    expect(html).not.toContain('尚未实现：取整规则存在歧义。');
    expect(html).not.toContain('sizefmt/core.py');
    expect(html).not.toContain('**');
    expect(html).not.toContain('ls sizefmt/__init__.py');
    expect(html).toContain('data-open="false"');
  });

  it('shows the server role log even when no dialogue rows are loaded', () => {
    const html = renderToStaticMarkup(createElement(EventStream, {
      events: [], snapshot: roleSnapshot([said('Checking the sources now.', { agent_layer: 'manager' })]),
      connected: true, showReasoning: false,
    }));
    expect(html).toContain('data-role="manager"');
    expect(html).not.toContain('Checking the sources now.');
  });

  it("colours a role's dot and name, never what it wrote", () => {
    const html = renderToStaticMarkup(createElement(EventStream, {
      events: [], snapshot: roleSnapshot([{ type: 'round.review.completed', agent_layer: 'reviewer', review_source: 'reviewer', status: 'engineer', reason: 'Fix the off-by-one.', item_id: 't1', ts: (clock += 1) }]),
      connected: true,
      showReasoning: false,
    }));
    const lane = html.slice(html.indexOf('data-role="reviewer"'));
    expect(lane).not.toContain('Fix the off-by-one.');
    // The verdict's mark may take its tone; the words it says do not.
    expect(lane).not.toContain('data-role-headline');
    expect(lane).not.toContain('style=');
  });

  it('keeps task progress out of the collapsed role process panel', () => {
    const task = (id: string, title: string) => ({ id, title, objective: '', status: 'in_progress', priority: 1 });
    const reviewer = { role: 'reviewer', backend: 'codex', backend_label: 'Codex', model: '', effort: null, active: true, label: 'Reviewer', status: 'running', age_s: null, tasks: ['t1', 't2'] };
    const snapshot = { session: { id: 's' }, daemon: { alive: true }, roles: [reviewer], backlog: [task('t1', 'Write primes.py'), task('t2', 'Write fib.py')] } as unknown as Snapshot;
    const html = renderToStaticMarkup(createElement(EventStream, {
      events: [], snapshot: roleSnapshot([
        said('Checking primes.py.', { agent_layer: 'reviewer', item_id: 't1' }),
        said('Checking fib.py.', { agent_layer: 'reviewer', item_id: 't2' }),
      ], { ...snapshot, work_status: { state: 'running', roles: ['reviewer'], role: 'reviewer', taskId: '', title: '', reason: '', foreground: false, activityAt: null, label: { 'zh-CN': '', en: '' } } }),
      connected: true,
      showReasoning: false,
    }));
    const lane = html.slice(html.indexOf('data-role="reviewer"'));

    expect(lane).not.toContain('data-role-task');
    expect(lane).not.toContain('Checking primes.py.');
    expect(lane).not.toContain('Checking fib.py.');
    // Records stay folded until the reader opens the role.
    expect(lane).not.toContain('data-row-task=');
  });
});
