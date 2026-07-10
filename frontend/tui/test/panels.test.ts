import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { test } from 'node:test';
import React from 'react';
import { Box, render } from 'ink';
import { PanelView, type PanelState } from '../src/components/panels.js';
import { Header } from '../src/components/Header.js';
import { Footer } from '../src/components/Footer.js';
import { ThinkingLine } from '../src/components/ThinkingLine.js';
import { LiveActivity } from '../src/components/LiveActivity.js';
import { ActivityPane } from '../src/components/ActivityPane.js';
import { PromptBox } from '../src/components/PromptBox.js';
import type { EventMsg, Snapshot } from '../src/api.js';

const ANSI = /\u001B\[[0-?]*[ -/]*[@-~]/g;

async function renderNode(node: React.ReactElement, width: number): Promise<string> {
  const stdout = new PassThrough() as PassThrough & {
    columns: number;
    rows: number;
    isTTY: boolean;
  };
  stdout.columns = width;
  stdout.rows = 24;
  // Width still comes from ``columns``; keeping this false avoids installing a
  // process-wide cursor-restoration hook that would leak ANSI into TAP output.
  stdout.isTTY = false;
  let output = '';
  stdout.on('data', (chunk) => { output += String(chunk); });
  const instance = render(
    node,
    { stdout: stdout as never, debug: true, exitOnCtrlC: false, patchConsole: false },
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  instance.unmount();
  await new Promise((resolve) => setTimeout(resolve, 5));
  return output.replace(ANSI, '');
}

async function renderPanel(
  panel: PanelState,
  width: number,
  options: { snap?: Snapshot | null; events?: EventMsg[] } = {},
): Promise<string> {
  return renderNode(
    React.createElement(PanelView, {
      panel,
      snap: options.snap ?? null,
      events: options.events ?? [],
      viewportRows: 24,
      viewportColumns: width,
      activeProject: 's-live',
    }),
    width,
  );
}

test('60-column daemon picker keeps the focused row and switch hint visible', async () => {
  const output = await renderPanel({
    kind: 'daemons',
    selection: 1,
    data: [
      { id: 's-live', label: 'Live paper', objective: '', last_active: 2, daemon_alive: true, daemon_pid: 42, uptime_seconds: 3 },
      { id: 's-old', label: 'Old run', objective: '', last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
    ],
  }, 60);
  assert.match(output, /› ○ s-old/);
  assert.match(output, /Enter switch/);
  assert.ok(output.split('\n').every((line) => Array.from(line).length <= 60));
});

test('daemon picker filters by objective and exposes search/new shortcuts', async () => {
  const output = await renderPanel({
    kind: 'daemons',
    query: 'recursive live',
    selection: 0,
    data: [
      { id: 's-kernel', label: 'Kernel paper', objective: 'Reproduce recursive kernel benchmark', last_active: 2, daemon_alive: true, daemon_pid: 42, uptime_seconds: 3 },
      { id: 's-vision', label: 'Vision notes', objective: 'Review datasets', last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
    ],
  }, 60);
  assert.match(output, /recursive live/);
  assert.match(output, /Kernel paper/);
  assert.doesNotMatch(output, /Vision notes/);
  assert.match(output, /\/ search · n new/);
  assert.ok(output.split('\n').every((line) => Array.from(line).length <= 60));
});

test('artifact list and text preview render at narrow terminal widths', async () => {
  const artifact = {
    path: 'paper/result.md', name: 'result.md', why: 'reviewed output', exists: true,
    kind: 'text' as const, mime: 'text/markdown', size: 1536, mtime: 1,
    preview: '# Result\nAccuracy 78.9%\nNo leakage detected', truncated: false,
  };
  const list = await renderPanel({ kind: 'artifacts', selection: 0, data: [artifact] }, 60);
  assert.match(list, /› ◆ paper\/result\.md/);
  assert.match(list, /Enter preview/);
  const preview = await renderPanel({ kind: 'artifact', data: artifact }, 60);
  assert.match(preview, /Accuracy 78\.9%/);
  assert.ok(preview.split('\n').every((line) => Array.from(line).length <= 60));
});

test('connection health remains visible without overflowing a 60-column terminal', async () => {
  const health = 'snapshot refresh failed · GET /snapshot → 503: backend warming up';
  const output = await renderNode(
    React.createElement(Box, { flexDirection: 'column' },
      React.createElement(Header, { snap: null, connected: false, width: 60, health }),
      React.createElement(Footer, { notice: '', health, roles: [], width: 60 }),
    ),
    60,
  );
  assert.match(output, /snapshot refresh failed/);
  const finalFrame = output.slice(output.lastIndexOf('◆ argus'));
  assert.ok(finalFrame.split('\n').every((line) => Array.from(line).length <= 60));
});

test('pending Manager line exposes stop-waiting help at narrow widths', async () => {
  for (const width of [40, 60]) {
    const output = await renderNode(
      React.createElement(ThinkingLine, { tick: 2, phase: 'Manager · reading context', elapsedS: 3 }),
      width,
    );
    assert.match(output.replace(/\s+/g, ' '), /Esc stop waiting/);
    assert.ok(output.split('\n').every((line) => Array.from(line).length <= width));
  }
});

test('long multiline paste renders as a bounded single-line preview', async () => {
  const value = `first line\n${'long prompt '.repeat(20)}final line`;
  const output = await renderNode(
    React.createElement(PromptBox, {
      edit: { value, cursor: Array.from(value).length },
      width: 60,
    }),
    60,
  );
  assert.match(output, /·261/);
  assert.match(output.replace(/\s+/g, ' '), /final.*line/);
  assert.doesNotMatch(output, /first line/);
  assert.ok(output.split('\n').every((line) => Array.from(line).length <= 60));
});

test('Manager waiting line distinguishes the foreground message and hides handoff internals', async () => {
  const output = await renderNode(
    React.createElement(ThinkingLine, {
      tick: 2,
      phase: 'Manager · SELF: one Copilot handling [SESSION HANDOFF — internal context]',
      elapsedS: 5,
    }),
    120,
  );
  assert.match(output, /Your message/);
  assert.match(output, /context refreshed/);
  assert.doesNotMatch(output, /SESSION HANDOFF|internal context/);
});

test('live activity stays concise and the detail pane never prints raw prompts', async () => {
  const events: EventMsg[] = [{
    type: 'role.activity', activity_id: 'phase:idea-search', role: 'engineer',
    label: 'searching recent papers + generating candidate ideas', status: 'running',
    started_ts: Date.now() / 1000 - 5, ts: Date.now() / 1000, model: 'gpt-5.5',
    prompt: 'DO NOT SHOW THIS PROMPT',
  }];
  const live = await renderNode(React.createElement(LiveActivity, { events, width: 120, background: true }), 120);
  assert.match(live, /searching recent papers/);
  assert.match(live, /Background/);
  assert.match(live, /Ctrl\+O details/);
  const pane = await renderNode(React.createElement(ActivityPane, { events }), 120);
  assert.match(pane, /observable actions only/);
  assert.doesNotMatch(pane, /DO NOT SHOW/);
});

test('footer prefers the active call model over configured defaults', async () => {
  const roles = [{
    role: 'reviewer', backend: 'copilot', backend_label: 'Copilot',
    model: 'claude-sonnet-5', effort: null, active: true,
    label: 'thinking', status: 'running', age_s: 3,
  }];
  const active = {
    id: 'review-1', role: 'reviewer', label: 'reviewing evidence', detail: '',
    status: 'running' as const, startedTs: 1, updatedTs: 2, elapsedS: 1,
    model: 'gpt-5.5', backend: 'copilot', milestone: false,
  };
  const output = await renderNode(
    React.createElement(Footer, { notice: '', roles, active, width: 160 }),
    160,
  );
  assert.match(output, /reviewer.*Copilot.*gpt-5\.5/i);
  assert.doesNotMatch(output, /claude-sonnet-5/);
});

test('searchable event and full task panels stay useful at 60 columns', async () => {
  const events: EventMsg[] = [
    { type: 'round.main.completed', round_index: 2 },
    { type: 'life.lifecycle.block', reason: 'needs credentials', operator_alert: true },
  ];
  const feed = await renderPanel(
    { kind: 'events', filter: 'attention', query: 'credentials' },
    60,
    { events },
  );
  assert.match(feed, /Watch/);
  assert.match(feed, /needs credentials/);
  assert.doesNotMatch(feed, /round 2 completed/);

  const item = {
    id: 'task-123', title: 'Reproduce benchmark', objective: 'Run five seeds and verify there is no benchmark leakage.',
    status: 'running', priority: 10, max_cost_usd: 12, iterate: true,
    iteration_cycles_done: 2, iteration_max_cycles: 6, iteration_cost_usd: 3.5,
  };
  const task = await renderPanel({ kind: 'task', data: item }, 60);
  assert.match(task, /Run five seeds/);
  assert.ok(task.split('\n').every((line) => Array.from(line).length <= 60));

  const snap = {
    session: { id: 's', display_name: '', objective: '', last_active: 0, cwd: '' },
    daemon: { alive: true, pid: 1, uptime_seconds: 1, backend: 'x', per_mission_cap_usd: 1, daily_cap_usd: 1, global_daily_cap_usd: 0 },
    roles: [], recent_events: [],
    backlog: [item, { ...item, id: 'done-1', title: 'Old result', status: 'done' }],
  } as Snapshot;
  const backlog = await renderPanel({ kind: 'backlog', selection: 0 }, 60, { snap });
  assert.match(backlog, /› running/);
  assert.doesNotMatch(backlog, /Old result/);
});
