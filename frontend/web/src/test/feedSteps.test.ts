import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import type { EventMsg } from '../api';
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
import { EventStream, partitionRoleRows } from '../components/EventStream';

const here = dirname(fileURLToPath(import.meta.url));

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
    expect(stepTarget(step)).toBe('notes.md');
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
  const skipped = (round: number, ts: number): EventMsg => ({
    type: 'round.review.completed',
    review_skipped: true,
    backend_unavailable: true,
    round_index: round,
    reason: 'Engineer backend failed before a trustworthy completed turn; reviewer skipped.',
    ts,
  } as EventMsg);

  it('collapses consecutive identical rows into one row with a count and the latest clock', () => {
    const folded = fold([skipped(1, 10), skipped(2, 20), skipped(3, 30)]);
    expect(folded).toHaveLength(1);
    const step = folded[0] as FeedStep;
    expect(step.repeat).toBe(3);
    expect(step.latest.ts).toBe(30);
    expect(step.ev.ts).toBe(10);
  });

  it('collapses a failure streak whose only difference is the counter, showing the latest wording', () => {
    const failure = (streak: number, ts: number): EventMsg => ({
      type: 'round.reviewer_backend_failure',
      operator_alert: true,
      streak,
      threshold: 2,
      text: `reviewer backend unavailable ${streak}/2: no judgment was rendered — not continuing blind.`,
      ts,
    } as EventMsg);
    const folded = fold([failure(1, 1), failure(2, 2)]);
    expect(folded).toHaveLength(1);
    const step = folded[0] as FeedStep;
    expect(step.repeat).toBe(2);
    expect(step.r.text).toContain('2/2');
  });

  it('collapses a retry cycle that alternates two rows once per attempt', () => {
    const events: EventMsg[] = [];
    for (let attempt = 0; attempt < 6; attempt += 1) {
      events.push({ type: 'life.planner.start', objective: 'write the paper', ts: (clock += 1) } as EventMsg);
      events.push({ type: 'life.planner.error', error: 'Copilot CLI exited with code 1.', ts: (clock += 1) } as EventMsg);
    }
    events.push({ type: 'life.planner.start', objective: 'write the paper', ts: (clock += 1) } as EventMsg);
    events.push({ type: 'life.planner.task_added', title: 'Build the portfolio', ts: (clock += 1) } as EventMsg);
    const folded = fold(events) as FeedStep[];
    expect(folded.map((step) => [step.r.glyph, step.repeat])).toEqual([
      ['📋', 6], ['⚠', 6], ['📋', 1], ['＋', 1],
    ]);
  });

  it('folds rounds in which nothing happened, but not rounds with work between them', () => {
    const empty: EventMsg[] = [];
    for (let round = 4; round <= 9; round += 1) {
      empty.push({ type: 'round.start', round_index: round, ts: (clock += 1) } as EventMsg);
      empty.push({ type: 'round.main.completed', round_index: round, ts: (clock += 1) } as EventMsg);
    }
    const folded = fold(empty) as FeedStep[];
    expect(folded.map((step) => [step.r.text, step.repeat])).toEqual([['round 9', 6], ['finished round 9 of work', 6]]);

    const busy: EventMsg[] = [];
    for (let round = 1; round <= 3; round += 1) {
      busy.push({ type: 'round.start', round_index: round, ts: (clock += 1) } as EventMsg);
      busy.push(said(`Working on part ${round} of the proof.`));
      busy.push({ type: 'round.main.completed', round_index: round, ts: (clock += 1) } as EventMsg);
    }
    expect(fold(busy)).toHaveLength(9);
  });

  it('folds the nine "no review this round" rows whose only difference is the streak counter', () => {
    const events = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((round) => ({
      type: 'round.review.completed',
      status: 'continue',
      review_skipped: true,
      backend_unavailable: false,
      round_index: round,
      reason: `Engineer backend failed before a trustworthy completed turn; reviewer skipped. backend_failure_streak=${round}/2; error=Copilot CLI exited with code 1.`,
      ts: (clock += 1),
    } as EventMsg));
    const folded = fold(events) as FeedStep[];
    expect(folded).toHaveLength(1);
    expect(folded[0].repeat).toBe(9);
  });

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

describe('foldFeedRows · a real campaign', () => {
  const events = readFileSync(join(here, 'fixtures', 'campaign-s-3dba660b.events.jsonl'), 'utf8')
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as EventMsg);

  it('shrinks the feed of s-3dba660b without hiding anything', () => {
    const rendered = rows(events);
    const { roleRows, systemRows } = partitionRoleRows(rendered);
    const lists = [...Object.values(roleRows), systemRows];
    const before = lists.reduce((total, list) => total + list.length, 0);
    const foldedLists = lists.map((list) => foldFeedRows(list, 'en'));
    const after = foldedLists.reduce((total, list) => total + list.length, 0);
    const preserved = foldedLists.flat().reduce((total, row) => (
      total + (row.kind === 'group' ? row.steps.reduce((sum, step) => sum + step.repeat, 0) : row.repeat)
    ), 0);
    const echoes = events.filter((event) => event.type === 'engineer.progress' && event.kind === 'tool_use' && event.status === 'failed').length;

    // 622 events → 209 rendered rows → 127 rows once folded (manager 5→5,
    // planner 27→16, engineer 161→99, reviewer 13→5, Argus updates 3→2).
    expect(before).toBe(209);
    expect(after).toBeLessThanOrEqual(Math.round(before * 0.65));
    // Every rendered row is still reachable: as a step, a group member or a repeat.
    expect(preserved + echoes).toBe(before);

    // The Reviewer's thirteen rows become five: "checks round 1", the first
    // mission's two skipped reviews with the model-service retry between
    // them, then the nine "no review this round" rows of the second mission.
    const reviewer = foldFeedRows(roleRows.reviewer, 'en');
    expect(reviewer.map((row) => (row.kind === 'step' ? row.repeat : 0))).toEqual([1, 1, 1, 1, 9]);
    expect((reviewer[2] as FeedStep).r.text).toBe('reviewer backend unavailable; retrying after 15.0s');

    // The Planner's six failed attempts are two rows, not twelve.
    const planner = foldFeedRows(roleRows.planner, 'en');
    const errors = planner.filter((row) => row.kind === 'step' && row.ev.type === 'life.planner.error');
    expect(errors).toHaveLength(1);
    expect((errors[0] as FeedStep).repeat).toBe(6);

    // Reads, searches and fetches fold; shell commands do not.
    const engineer = foldFeedRows(roleRows.engineer, 'en');
    const groups = engineer.filter((row): row is FeedGroup => row.kind === 'group');
    expect(groups.length).toBeGreaterThan(0);
    expect(engineer.filter((row) => row.kind === 'step' && row.ev.kind === 'command_execution')).toHaveLength(
      roleRows.engineer.filter((row) => row.ev.kind === 'command_execution').length,
    );
    const summaries = groups.map((group) => groupSummary(group, en, 'en'));
    expect(summaries).toContain('Read 6 files: strategy.md, w10.jsonl, w12.jsonl…');
    expect(summaries).toContain('Looked up 4 web pages: arxiv.org/abs/1706.03762, arxiv.org/abs/2312.00752, arxiv.org/abs/2405.21060… · 4 failed');
    expect(summaries.some((summary) => summary.startsWith('Used '))).toBe(false);

    // Six rounds in which the model service failed before any work read as
    // three rows, not eighteen — and the failure is named, not just counted.
    const rounds = engineer.filter((row): row is FeedStep => row.kind === 'step' && row.ev.type === 'round.start');
    expect(rounds.map((row) => row.repeat)).toContain(6);
    const failures = engineer.filter((row): row is FeedStep => row.kind === 'step' && row.ev.type === 'round.backend_failure.backoff');
    expect(failures.map((row) => row.repeat)).toEqual([2, 6, 1]);
    expect(failures[1].r.text).toContain('Copilot CLI exited with code 1');
    expect(failures[1].r.tone).toBe('warn');
  });
});

describe('EventStream renders folded rows', () => {
  it('shows a tool group as one expandable line and a repeated sentence with its count', () => {
    const html = renderToStaticMarkup(createElement(EventStream, {
      // The Planner's three identical errors come first; the Engineer, being
      // the role that spoke last, is the group that opens on a live feed.
      events: [
        { type: 'life.planner.error', error: 'Copilot CLI exited with code 1.', ts: 1 },
        { type: 'life.planner.error', error: 'Copilot CLI exited with code 1.', ts: 2 },
        { type: 'life.planner.error', error: 'Copilot CLI exited with code 1.', ts: 3 },
        call('view', { path: 'a.py' }),
        call('view', { path: 'b.py' }),
        call('view', { path: 'c.py' }),
        shell('pytest -q'),
        shell('pytest -q'),
      ] as EventMsg[],
      connected: true,
      showReasoning: true,
      onToggleReasoning: () => undefined,
    }));

    expect(html).toContain('Read 3 files: a.py, b.py, c.py');
    expect(html).toContain('data-feed-group="read" data-open="false"');
    // Members wait behind the summary until it is opened, from a real button.
    expect(html).not.toContain('view: {');
    expect(html).toMatch(/<button type="button" aria-expanded="false" title="Show each step"/);
    // Two identical shell commands fold to one line with a count.
    expect(html).toContain('data-repeat="2"');
    // The collapsed Planner header still previews its folded row.
    expect(html).toContain('the Planner hit an error');
    expect(html).not.toContain('Used ');
  });
});
