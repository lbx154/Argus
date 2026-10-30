import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { test } from 'node:test';
import React from 'react';
import { Box, render } from 'ink';
import stringWidth from 'string-width';
import { PanelView, type PanelState } from '../src/components/panels.js';
import { Header } from '../src/components/Header.js';
import { Footer } from '../src/components/Footer.js';
import { ThinkingLine } from '../src/components/ThinkingLine.js';
import { EventLog } from '../src/components/EventLog.js';
import { PromptBox } from '../src/components/PromptBox.js';
import {
  SlashMenu,
  slashMenuVisibleRows,
  slashMenuWindow,
} from '../src/components/SlashMenu.js';
import { CostGauge } from '../src/components/CostGauge.js';
import { PendingDecisionPrompt } from '../src/components/PendingDecisionPrompt.js';
import type { BacklogItem, EventMsg, ResourceStatus, Snapshot, StatusView, UsageSummary } from '../src/api.js';
import { SLASH_COMMANDS } from '../src/input/slash.js';

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

async function renderInteractiveNode(
  node: React.ReactElement,
  width: number,
  rows: number,
): Promise<string> {
  const stdout = new PassThrough() as PassThrough & {
    columns: number;
    rows: number;
    isTTY: boolean;
  };
  stdout.columns = width;
  stdout.rows = rows;
  stdout.isTTY = true;
  let output = '';
  stdout.on('data', (chunk) => { output += String(chunk); });
  const instance = render(
    node,
    { stdout: stdout as never, debug: false, exitOnCtrlC: false, patchConsole: false },
  );
  await new Promise((resolve) => setTimeout(resolve, 40));
  instance.rerender(React.cloneElement(node));
  await new Promise((resolve) => setTimeout(resolve, 40));
  instance.unmount();
  await new Promise((resolve) => setTimeout(resolve, 5));
  return output;
}

async function renderPanel(
  panel: PanelState,
  width: number,
  options: { snap?: Snapshot | null; events?: EventMsg[]; viewportRows?: number } = {},
): Promise<string> {
  return renderNode(
    React.createElement(PanelView, {
      panel,
      snap: options.snap ?? null,
      events: options.events ?? [],
      viewportRows: options.viewportRows ?? 24,
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
      { workdir: '/work', id: 's-live', label: 'Live paper', last_active: 2, daemon_alive: true, daemon_pid: 42, },
      { workdir: '/work', id: 's-old', label: 'Old run', last_active: 1, daemon_alive: false, daemon_pid: null, },
    ],
  }, 60);
  assert.match(output, /› ○ s-old/);
  assert.match(output, /Enter switch/);
  assert.ok(output.split('\n').every((line) => Array.from(line).length <= 60));
});

test('daemon picker filters by name and exposes search/new shortcuts', async () => {
  const output = await renderPanel({
    kind: 'daemons',
    query: 'kernel live',
    selection: 0,
    data: [
      { workdir: '/work', id: 's-kernel', label: 'Kernel paper', last_active: 2, daemon_alive: true, daemon_pid: 42, },
      { workdir: '/work', id: 's-vision', label: 'Vision notes', last_active: 1, daemon_alive: false, daemon_pid: null, },
    ],
  }, 60);
  assert.match(output, /kernel live/);
  assert.match(output, /Kernel paper/);
  assert.doesNotMatch(output, /Vision notes/);
  assert.match(output, /\/ search · n new/);
  assert.ok(output.split('\n').every((line) => Array.from(line).length <= 60));
});

test('artifact list and text preview render at narrow terminal widths', async () => {
  const artifact = {
    path: 'paper/result.md', name: 'result.md',
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
      React.createElement(Header, { width: 60, health }),
      React.createElement(Footer, { notice: '', health, width: 60 }),
    ),
    60,
  );
  assert.match(output, /snapshot refresh failed/);
  const finalFrame = output.slice(output.lastIndexOf('◆ ARGUS'));
  assert.ok(finalFrame.split('\n').every((line) => Array.from(line).length <= 60));

  const warning = await renderNode(
    React.createElement(Footer, {
      notice: 'warning: backend code and installed artifacts differ',
      width: 60,
    }),
    60,
  );
  assert.match(warning, /installed artifacts differ/);
});

test('header uses a neutral lab identity outside research missions', async () => {
  const output = await renderNode(
    React.createElement(Header, { width: 120 }),
    120,
  );
  const researchOutput = await renderNode(
    React.createElement(Header, { width: 120, vertical: 'research' }),
    120,
  );
  assert.match(output, /Autonomous Work Lab/);
  assert.match(researchOutput, /Autonomous Research Lab/);
  assert.doesNotMatch(output, /pid|backend|daily cap/);
});


test('operator decision prompt shows the full blocker and answer controls', async () => {
  const card = {
    id: 'decision-1',
    item_id: 'mission-1',
    revision: 1,
    status: 'pending' as const,
    title: 'macOS Accessibility permission required',
    reason: 'The verification process cannot control System Events.',
    question: (
      'Open System Settings → Privacy & Security → Accessibility, enable '
      + 'Terminal and Node, then return here to retry the verification.'
    ),
    evidence: [],
    options: [
      {
        id: 'retry',
        label: 'Permission enabled — retry',
        description: 'Resume the same mission and rerun the verification.',
      },
      {
        id: 'custom',
        label: 'Give different guidance',
        description: 'Tell Argus what to do instead.',
      },
    ],
    selected_option: '',
    note: '',
  };
  const output = await renderNode(React.createElement(PendingDecisionPrompt, {
    card,
    selection: 1,
    note: { value: 'Use the signed helper app', cursor: 25 },
    busy: false,
    error: '',
  }), 80);

  assert.match(output, /ACTION REQUIRED/);
  assert.match(output, /System Settings/);
  assert.match(output, /Permission enabled — retry/);
  assert.match(output, /Use the signed helper app/);
  assert.match(output, /Enter confirm/);
  assert.ok(output.split('\n').every((line) => stringWidth(line) <= 80));
});

test('operator decision prompt never fabricates choices when the agent supplied none', async () => {
  const output = await renderNode(React.createElement(PendingDecisionPrompt, {
    card: {
      id: 'decision-freeform',
      item_id: 'mission-freeform',
      revision: 1,
      status: 'pending',
      title: 'Accessibility permission required',
      reason: '',
      question: 'Which terminal should receive Accessibility permission?',
      evidence: [],
      options: [],
      options_source: 'none',
      selected_option: '',
      note: '',
    },
    selection: 0,
    note: { value: 'Visual Studio Code', cursor: 18 },
    busy: false,
    error: '',
  }), 80);

  assert.match(output, /Your response › Visual Studio Code/);
  assert.match(output, /Type your answer · Enter send/);
  assert.doesNotMatch(output, /按建议继续|给出其他指示|保留当前结果并停止/);
});

test('operations panel owns cost, pid, backend, and model details', async () => {
  const snap = {
    session: { id: 's-ops', display_name: '', objective: '', last_active: 0, workdir: '' },
    daemon: {
      alive: true, pid: 42, uptime_seconds: 600, backend: 'copilot', backend_label: 'Copilot',
    },
    roles: [{
      role: 'engineer', backend: 'copilot', backend_label: 'Copilot', model: 'gpt-5.6-sol',
      effort: 'xhigh', active: false, label: 'idle', status: 'idle', age_s: null,
    }],
    backlog: [], usage_summary: { call_count: 3, cost_usd: 1.25, pricing_status: 'priced', input_tokens: 10, output_tokens: 2 },
  } as Snapshot;
  const output = await renderPanel({ kind: 'operations' }, 60, { snap, viewportRows: 40 });
  assert.match(output, /pid 42/);
  assert.match(output, /Copilot/);
  assert.match(output, /gpt-5\.6-sol/);
  assert.match(output, /model\/API spend \$1\.25/);
  assert.doesNotMatch(output, /protocol|—\/—\.—/);
  assert.ok(output.split('\n').every((line) => stringWidth(line) <= 60));
});

test('status panel renders degraded resource status', async () => {
  const status: StatusView = {
    identity: '',
    backlog_pending: [],
    pending_questions: [],
    journal: [],
    continuous: { enabled: false, objective: '' },
    inbox_pending: 0,
    daemon: {
      alive: true,
      pid: 42,
      uptime_seconds: 60,
      backend: 'codex',
    },
    roles: [],
    active_role: null,
  };
  const resources: ResourceStatus = {
    schema_version: 1,
    enforcement: 'advisory',
    accelerators: [
      { kind: 'cuda', status: 'degraded', device_count: 1, detail: 'could not parse one telemetry row' },
      { kind: 'rocm', status: 'absent', device_count: 0, detail: 'rocm-smi is not installed' },
    ],
  };

  const output = await renderPanel({ kind: 'status', data: [status, resources] }, 70, { viewportRows: 40 });

  assert.match(output, /resources\s+advisory mode/);
  assert.match(output, /CUDA\s+degraded · 1 devices/);
  assert.match(output, /could not parse one telemetry row/);
  assert.ok(output.split('\n').every((line) => stringWidth(line) <= 70));
});

const usageSummary = (overrides: Partial<UsageSummary> = {}): UsageSummary => ({
  call_count: 1, known_cost_usd: 0, cost_usd: 0, pricing_status: 'priced',
  priced_calls: 1, partial_calls: 0, unpriced_calls: 0, not_billed_calls: 0,
  input_tokens: 0, cached_input_tokens: 0, cache_write_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0,
  premium_requests: 0, total_nano_aiu: 0, premium_request_cost_usd: 0,
  ...overrides,
});

test('partial usage never renders as a zero-dollar cumulative cost', async () => {
  const output = await renderNode(
    React.createElement(CostGauge, {
      usage: usageSummary({ cost_usd: null, pricing_status: 'partial' }),
      width: 120,
    }),
    120,
  );
  assert.match(output, /model\/API spend partial/);
  assert.doesNotMatch(output, /\$0\.00/);
});

test('fresh project renders zero spend for itself and for today', async () => {
  const output = await renderNode(React.createElement(CostGauge, { width: 120 }), 120);
  assert.match(output, /model\/API spend \$0\.00/);
  assert.doesNotMatch(output, /cap|remaining|paused/);
});

test('usage gauge shows token inputs behind model API spend', async () => {
  const output = await renderNode(
    React.createElement(CostGauge, {
      usage: {
        call_count: 2,
        known_cost_usd: 0.31624875,
        cost_usd: 0.31624875,
        pricing_status: 'priced',
        priced_calls: 2,
        partial_calls: 0,
        unpriced_calls: 0,
        not_billed_calls: 0,
        input_tokens: 50505,
        cached_input_tokens: 0,
        cache_write_tokens: 0,
        output_tokens: 20,
        reasoning_output_tokens: 0,
        premium_requests: 2,
        total_nano_aiu: 31624875000,
        premium_request_cost_usd: 0.08,
      },
      width: 120,
    }),
    120,
  );
  assert.match(output, /model\/API spend \$0\.32/);
  assert.match(output, /tokens · input 50505/);
  assert.match(output, /output 20 · reasoning 0 · premium 2\.0/);
});

test('pending Manager line exposes stop-waiting help at narrow widths', async () => {
  for (const width of [40, 60]) {
    const output = await renderNode(
      React.createElement(ThinkingLine, { tick: 2, elapsedS: 3 }),
      width,
    );
    assert.match(output.replace(/\s+/g, ' '), /Esc stop waiting/);
    assert.ok(output.split('\n').every((line) => Array.from(line).length <= width));
  }
});

test('slash menu scales with terminal height while retaining a bounded ceiling', () => {
  assert.equal(slashMenuVisibleRows(16), 3);
  assert.equal(slashMenuVisibleRows(20), 7);
  assert.equal(slashMenuVisibleRows(24), 8);
  assert.equal(slashMenuVisibleRows(80), 8);
});

test('slash menu scroll window keeps the selected command visible without jumping early', () => {
  let view = slashMenuWindow(20, 0, 5);
  assert.deepEqual(view, { start: 0, end: 5, selected: 0 });
  view = slashMenuWindow(20, 4, 5, view.start);
  assert.deepEqual(view, { start: 0, end: 5, selected: 4 });
  view = slashMenuWindow(20, 5, 5, view.start);
  assert.deepEqual(view, { start: 1, end: 6, selected: 5 });
  view = slashMenuWindow(20, 4, 5, view.start);
  assert.deepEqual(view, { start: 1, end: 6, selected: 4 });
  view = slashMenuWindow(20, 0, 5, view.start);
  assert.deepEqual(view, { start: 0, end: 5, selected: 0 });
});

test('slash menu renders a short window above a still-visible prompt', async () => {
  const output = await renderNode(
    React.createElement(
      Box,
      { flexDirection: 'column' },
      React.createElement(SlashMenu, {
        items: SLASH_COMMANDS,
        selected: 0,
        maxVisible: 3,
      }),
      React.createElement(PromptBox, {
        edit: { value: '/', cursor: 1 },
        width: 60,
      }),
    ),
    60,
  );
  const finalFrame = output.slice(output.lastIndexOf('❯ /status'));
  assert.match(finalFrame, /\/status/);
  assert.doesNotMatch(finalFrame, /\/quit/);
  assert.match(finalFrame, new RegExp(`1/${SLASH_COMMANDS.length}`));
  assert.match(finalFrame, /talk to Argus/);
  assert.ok(finalFrame.split('\n').length <= 9);
  assert.ok(finalFrame.split('\n').every((line) => stringWidth(line) <= 60));
});

test('collapsing the event log preserves Static history without replaying it', async () => {
  const stdout = new PassThrough() as PassThrough & {
    columns: number;
    rows: number;
    isTTY: boolean;
  };
  stdout.columns = 60;
  stdout.rows = 24;
  stdout.isTTY = false;
  let output = '';
  stdout.on('data', (chunk) => { output += String(chunk); });
  const events: EventMsg[] = [{ type: 'ui.operator', text: 'STATIC_ONCE_MARKER', ts: 1 }];
  const view = (collapsed: boolean) => React.createElement(EventLog, {
    events,
    width: 60,
    mode: 'conversation',
    collapsed,
  });
  const instance = render(
    view(false),
    { stdout: stdout as never, debug: false, exitOnCtrlC: false, patchConsole: false },
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  instance.rerender(view(true));
  await new Promise((resolve) => setTimeout(resolve, 20));
  instance.rerender(view(false));
  await new Promise((resolve) => setTimeout(resolve, 20));
  instance.unmount();
  await new Promise((resolve) => setTimeout(resolve, 5));
  const clean = output.replace(ANSI, '');
  assert.equal(clean.split('STATIC_ONCE_MARKER').length - 1, 1);
});

test('conversation feed shows reasoning softly and can hide it', async () => {
  const events: EventMsg[] = [{
    type: 'engineer.progress',
    kind: 'reasoning',
    text: 'REASONING_SUMMARY_MARKER',
    agent_layer: 'engineer',
    ts: 1,
  }];
  const visible = await renderNode(React.createElement(EventLog, {
    events,
    width: 80,
    mode: 'conversation',
    showReasoning: true,
  }), 80);
  const hidden = await renderNode(React.createElement(EventLog, {
    events,
    width: 80,
    mode: 'conversation',
    showReasoning: false,
    showIdle: false,
  }), 80);
  assert.match(visible.replace(ANSI, ''), /REASONING_SUMMARY_MARKER/);
  assert.doesNotMatch(hidden.replace(ANSI, ''), /REASONING_SUMMARY_MARKER/);
});

test('final Planner delivery wraps instead of truncating its tail', async () => {
  const output = await renderNode(React.createElement(EventLog, {
    events: [{
      type: 'engineer.progress',
      kind: 'agent_message',
      agent_layer: 'planner',
      text: `Audit result ${'x'.repeat(240)}\nPROJECT_DONE=true\nREASON=FINAL_TAIL_VISIBLE`,
      final_delivery: true,
      ts: 1,
    } as EventMsg],
    width: 60,
    mode: 'all',
    showIdle: false,
  }), 60);

  assert.match(output.replace(ANSI, ''), /FINAL_TAIL_VISIBLE/);
});



test('24-row operations panel stays below Ink full-screen clear threshold', async () => {
  const snap = {
    session: { id: 's-ops', display_name: '', objective: '', last_active: 0, workdir: '' },
    daemon: {
      alive: false, pid: null, uptime_seconds: null, backend: 'copilot', backend_label: 'Copilot',
    },
    roles: ['manager', 'planner', 'engineer', 'reviewer'].map((role) => ({
      role, backend: 'copilot', backend_label: 'Copilot', model: 'gpt-5.5',
      effort: 'xhigh', active: false, label: 'idle', status: 'idle', age_s: null,
    })),
    backlog: [], spend_usd: 0, spend_status: 'priced',
    usage_summary: {
      input_tokens: 16_296, cached_input_tokens: 14_848, cache_write_tokens: 0,
      output_tokens: 103, reasoning_output_tokens: 61, call_count: 1,
    },
  } as Snapshot;
  const events: EventMsg[] = [{
    type: 'role.activity', activity_id: 'manager-request', role: 'manager',
    label: 'handling the operator request', status: 'running', ts: Date.now() / 1000,
  }];
  const output = await renderNode(
    React.createElement(
      Box,
      { flexDirection: 'column' },
      React.createElement(Header, { width: 99 }),
      React.createElement(PanelView, {
        panel: { kind: 'operations' },
        snap,
        events,
        viewportRows: 24,
        viewportColumns: 99,
        activeProject: 's-ops',
      }),
    ),
    99,
  );
  const finalFrame = output.slice(output.lastIndexOf('◉ argus'));
  assert.ok(finalFrame.trimEnd().split('\n').length < 24);
  assert.match(finalFrame, /activity\s+manager · handling the operator request/);
});

test('long input wraps while keeping a bounded view around the caret', async () => {
  const value = `first line\n${'long prompt '.repeat(20)}final line`;
  const output = await renderNode(
    React.createElement(PromptBox, {
      edit: { value, cursor: Array.from(value).length },
      width: 60,
    }),
    60,
  );
  assert.match(output, /261 chars/);
  assert.match(output.replace(/\s+/g, ' '), /final.*line/);
  assert.doesNotMatch(output, /first line/);
  assert.ok(output.split('\n').length > 3);
  assert.ok(output.split('\n').every((line) => Array.from(line).length <= 60));
});

test('an unclipped prompt automatically wraps instead of truncating', async () => {
  const value = '1234567890'.repeat(8);
  const output = await renderNode(
    React.createElement(PromptBox, {
      edit: { value, cursor: Array.from(value).length },
      width: 60,
    }),
    60,
  );
  const finalFrame = output.slice(output.lastIndexOf('╭'));
  assert.equal((finalFrame.match(/\d/g) ?? []).length, value.length);
  assert.doesNotMatch(finalFrame, /chars/);
  assert.ok(finalFrame.split('\n').length >= 5);
  assert.ok(finalFrame.split('\n').every((line) => Array.from(line).length <= 60));
});

test('wrapped prompt respects terminal cell widths for CJK text', async () => {
  const value = `${'前'.repeat(80)}光标位置${'后'.repeat(80)}`;
  const output = await renderNode(
    React.createElement(PromptBox, {
      edit: { value, cursor: 82 },
      width: 60,
    }),
    60,
  );
  const finalFrame = output.slice(output.lastIndexOf('╭'));
  assert.match(finalFrame, /光标位置/);
  assert.match(finalFrame, /164 chars/);
  // Ink/string-width may use one additional physical row across Node builds,
  // while the actual terminal-cell contract below remains the authority.
  assert.ok(finalFrame.split('\n').length <= 8);
  assert.ok(finalFrame.split('\n').every((line) => stringWidth(line) <= 60));
});

test('Manager waiting line distinguishes the foreground message and hides handoff internals', async () => {
  const output = await renderNode(
    React.createElement(ThinkingLine, {
      tick: 2,
      elapsedS: 5,
    }),
    120,
  );
  assert.match(output, /Your message/);
  assert.match(output, /Replying to your message/);
  assert.doesNotMatch(output, /SESSION HANDOFF|internal context/);
});

test('footer exposes only stable cockpit controls', async () => {
  const output = await renderNode(
    React.createElement(Footer, { notice: '', width: 160 }),
    160,
  );
  assert.match(output, /Enter send/);
  assert.match(output, /Ctrl-R rewrite/);
  assert.doesNotMatch(output, /Ctrl\+O|Ctrl\+T|Copilot|gpt-|pid/);
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
  assert.doesNotMatch(feed, /round 2 of work/);

  const item: BacklogItem = {
    id: 'task-123', title: 'Reproduce benchmark', objective: 'Run five seeds and verify there is no benchmark leakage.',
    status: 'in_progress', priority: 0, deps: [],
    notes: 'Engineer to the Reviewer: Five seeds checked.', last_error: 'codex exited with code 1',
  };
  const task = await renderPanel({ kind: 'task', data: item }, 60);
  assert.match(task, /Run five seeds/);
  assert.match(task, /Five seeds checked/);
  assert.match(task, /codex exited with code 1/);
  assert.doesNotMatch(task, /iteration|outcome/);
  assert.ok(task.split('\n').every((line) => Array.from(line).length <= 60));

  const snap = {
    session: { id: 's', display_name: '', objective: '', last_active: 0, workdir: '' },
    daemon: { alive: true, pid: 1, uptime_seconds: 1, backend: 'x'},
    roles: [], backlog: [
      { id: item.id, title: item.title, status: item.status, priority: item.priority, deps: item.deps },
      { id: 'done-1', title: 'Old result', status: 'done', priority: 0, deps: [] },
    ],
  } as Snapshot;
  const backlog = await renderPanel({ kind: 'backlog', selection: 0 }, 60, { snap });
  assert.match(backlog, /› in_progress/);
  assert.doesNotMatch(backlog, /Old result/);
});

test('workflow choice card shows request, options and optional note in a narrow terminal', async () => {
  const { operatorDecisionCards } = await import('../../core/src/decisions.js');
  const [card] = operatorDecisionCards([{ operator_decision: {
    id: 'intake-1', kind: 'domain_intake', status: 'pending', item_id: '', title: '选择处理方式',
    task_title: '给我解释日历日期', question: '你希望怎么处理？', options_source: 'workflow',
    options: [{ id: 'direct', label: '直接做', description: '单个 agent 处理' },
      { id: 'build', label: '建立专门流程', description: '确认需求并查资料' }],
  } }], []);
  const output = await renderNode(React.createElement(PendingDecisionPrompt, {
    card, selection: 1, note: { value: '注明来源', cursor: 4 }, busy: false, error: '',
  }), 40);
  assert.match(output, /选择处理方式/);
  assert.match(output, /给我解释日历日期/);
  assert.match(output, /› 2. 建立专门流程/);
  assert.match(output, /注明来源/);
  assert.doesNotMatch(output, /ACTION REQUIRED/);
  assert.ok(output.split('\n').every(line => stringWidth(line) <= 40));
});
