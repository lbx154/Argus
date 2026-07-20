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
import { LiveActivity } from '../src/components/LiveActivity.js';
import { ActivityPane } from '../src/components/ActivityPane.js';
import { EventLog } from '../src/components/EventLog.js';
import { PromptBox } from '../src/components/PromptBox.js';
import {
  SlashMenu,
  slashMenuVisibleRows,
  slashMenuWindow,
} from '../src/components/SlashMenu.js';
import { DaemonReplacementPicker } from '../src/components/DaemonReplacementPicker.js';
import { CostGauge } from '../src/components/CostGauge.js';
import { MissionCockpit } from '../src/components/MissionCockpit.js';
import { emptyMissionView } from '../../core/src/missionView.js';
import type { EventMsg, Snapshot } from '../src/api.js';
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
      React.createElement(Header, { width: 60, health }),
      React.createElement(Footer, { notice: '', health, width: 60 }),
    ),
    60,
  );
  assert.match(output, /snapshot refresh failed/);
  const finalFrame = output.slice(output.lastIndexOf('◆ ARGUS'));
  assert.ok(finalFrame.split('\n').every((line) => Array.from(line).length <= 60));
});

test('header establishes the autonomous research lab identity without ops clutter', async () => {
  const output = await renderNode(
    React.createElement(Header, { width: 120 }),
    120,
  );
  assert.match(output, /Autonomous Research Lab/);
  assert.doesNotMatch(output, /pid|backend|daily cap/);
});

test('mission cockpit keeps mission, team, and timeline readable at 60 columns', async () => {
  const view = emptyMissionView();
  view.mission.objective = 'Optimize FlashAttention on B200 beyond 65% SOL';
  view.mission.elapsed_seconds = 8040;
  view.stage = { id: 'optimize', label: 'Optimize' };
  view.round = { current: 7, max: 24 };
  view.roles.find((role) => role.role === 'planner')!.status = 'active';
  view.roles.find((role) => role.role === 'planner')!.label = 'Comparing 3 branches';
  view.timeline = [{
    id: 'e1', ts: 1, type: 'research.metric.reported', role: 'engineer',
    title: 'Metric reported', detail: '61.8% SOL', tone: 'metric',
  }];
  const output = await renderNode(React.createElement(MissionCockpit, { view, width: 60 }), 60);
  assert.match(output, /MISSION/);
  assert.match(output, /AI RESEARCH TEAM/);
  assert.match(output, /LIVE RESEARCH TIMELINE/);
  assert.match(output, /Comparing 3 branches/);
  assert.match(output, /● Comparing 3 branches/);
  assert.doesNotMatch(output, /[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/);
  assert.ok(output.split('\n').every((line) => stringWidth(line) <= 60));
});

test('operations panel owns cost, quota, pid, backend, and model details', async () => {
  const missionView = emptyMissionView();
  missionView.storage.project_skill_dir = '/state/project/skills';
  missionView.storage.project_skill_count = 3;
  missionView.storage.wiki_paths = ['/workspace/.autors/demo/wiki'];
  missionView.storage.skill_history_compressed = 4;
  missionView.storage.wiki_retired_compressed = 2;
  missionView.storage.skill_history_bytes_saved = 1024;
  missionView.storage.wiki_retired_bytes_saved = 512;
  const snap = {
    session: { id: 's-ops', display_name: '', objective: '', last_active: 0, cwd: '' },
    daemon: {
      alive: true, pid: 42, uptime_seconds: 600, backend: 'copilot', backend_label: 'Copilot',
      global_daily_cap_usd: 200,
      protocol: { name: 'argus.daemon', major: 1, minor: 1 },
    },
    roles: [{
      role: 'engineer', backend: 'copilot', backend_label: 'Copilot', model: 'gpt-5.6-sol',
      effort: 'xhigh', active: false, label: 'idle', status: 'idle', age_s: null,
    }],
    backlog: [], recent_events: [], global_spend_usd: 12.5, global_spend_status: 'priced',
    request_usage: {
      day: '2026-07-11',
      codex: { provider: 'codex', day: '2026-07-11', daily_calls: 9, daily_cap: 300, remaining: 291 },
      copilot: { provider: 'copilot', day: '2026-07-11', daily_calls: 403, daily_cap: 1000, remaining: 597, premium_requests: 551, premium_cap: 1000 },
    },
    mission_view: missionView,
  } as Snapshot;
  const output = await renderPanel({ kind: 'operations' }, 60, { snap, viewportRows: 40 });
  assert.match(output, /pid 42/);
  assert.match(output, /Copilot/);
  assert.match(output, /gpt-5\.6-sol/);
  assert.match(output, /host-global cost/);
  assert.match(output, /403\/1000/);
  assert.match(output, /self-evolution storage/);
  assert.match(output, /\/state\/project\/skills/);
  assert.match(output, /\.autors\/demo\/wiki/);
  assert.match(output, /skill 4 · wiki 2 · 1\.5 KB saved/);
  assert.ok(output.split('\n').every((line) => stringWidth(line) <= 60));
});

test('daemon replacement picker shows running work and state-preservation promise', async () => {
  const output = await renderNode(
    React.createElement(DaemonReplacementPicker, {
      width: 100,
      state: {
        targetProject: 's-new',
        running: [{
          id: 's-old',
          label: 'Long benchmark',
          objective: '',
          last_active: 1,
          daemon_alive: true,
          daemon_pid: 42,
          uptime_seconds: 120,
          active_role: 'engineer',
          activity: 'running held-out benchmark',
        }],
        limit: 1,
        activeCount: 1,
        selection: 0,
        resumeContinuous: false,
        busy: false,
        error: '',
      },
    }),
    100,
  );
  assert.match(output, /Concurrent work limit reached/);
  assert.match(output, /Long benchmark/);
  assert.match(output, /running held-out benchmark/);
  assert.match(output, /checkpoints, skills, and wiki stay/);
  assert.match(output, /saved\./);
});

test('request quotas render alongside monetary spend', async () => {
  const output = await renderNode(
    React.createElement(CostGauge, {
      daemon: undefined,
      width: 120,
      requestUsage: {
        day: '2026-07-11',
        codex: {
          provider: 'codex', day: '2026-07-11', daily_calls: 12,
          daily_cap: 300, remaining: 288,
        },
        copilot: {
          provider: 'copilot', day: '2026-07-11', daily_calls: 8,
          daily_cap: 100, remaining: 92, premium_requests: 2.5,
          premium_cap: 20, premium_remaining: 17.5,
        },
      },
    }),
    120,
  );
  assert.match(output, /Codex 12\/300/);
  assert.match(output, /Copilot 8\/100/);
  assert.match(output, /premium 2\.5\/20/);
});

test('cost control exposes in-flight reservations and unresolved pricing', async () => {
  const output = await renderNode(
    React.createElement(CostGauge, {
      settledUsd: 0.2,
      spendStatus: 'partial',
      daemon: undefined,
      width: 120,
      costControl: {
        day: '2026-07-11',
        active_reservations: 2,
        unresolved_calls: 1,
        unresolved: [],
        policy: 'block',
      },
    }),
    120,
  );
  assert.match(output, /in-flight 2/);
  assert.match(output, /unresolved 1/);
});

test('partial usage never renders as a zero-dollar cumulative cost', async () => {
  const output = await renderNode(
    React.createElement(CostGauge, {
      settledUsd: null,
      spendStatus: 'partial',
      daemon: undefined,
      width: 120,
    }),
    120,
  );
  assert.match(output, /host-global cost partial/);
  assert.doesNotMatch(output, /\$0\.00/);
});

test('fresh project renders zero global cost and configured global budget', async () => {
  const output = await renderNode(
    React.createElement(CostGauge, {
      settledUsd: null,
      spendStatus: 'empty',
      daemon: {
        alive: false,
        pid: null,
        uptime_seconds: null,
        backend: 'copilot',
        global_daily_cap_usd: 55,
      },
      width: 120,
    }),
    120,
  );
  assert.match(output, /host-global cost \$0\.00/);
  assert.match(output, /cap \$55\/d/);
});

test('usage gauge shows token inputs behind host-global cost', async () => {
  const output = await renderNode(
    React.createElement(CostGauge, {
      settledUsd: 0.31624875,
      spendStatus: 'priced',
      usageSummary: {
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
      daemon: undefined,
      width: 120,
    }),
    120,
  );
  assert.match(output, /host-global cost \$0\.32/);
  assert.match(output, /tokens · input 50505/);
  assert.match(output, /output 20/);
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

test('Manager heartbeat keeps rotating Argus phrases while reporting real silence', async () => {
  const first = await renderNode(
    React.createElement(ThinkingLine, {
      tick: 2,
      phase: 'Manager · waiting for the next model event · 12s quiet',
      heartbeat: true,
      quietS: 12,
      elapsedS: 12,
    }),
    100,
  );
  const second = await renderNode(
    React.createElement(ThinkingLine, {
      tick: 22,
      phase: 'Manager · waiting for the next model event · 14s quiet',
      heartbeat: true,
      quietS: 14,
      elapsedS: 14,
    }),
    100,
  );
  assert.match(first.replace(/\s+/g, ' '), /turning it over… · Manager alive · 12s quiet/);
  assert.match(second.replace(/\s+/g, ' '), /consulting a hundred eyes… · Manager alive · 14s quiet/);
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

test('19-row cockpit stays below Ink full-screen clear threshold', async () => {
  const view = emptyMissionView();
  const output = await renderNode(
    React.createElement(
      Box,
      { flexDirection: 'column' },
      React.createElement(Header, { width: 135 }),
      React.createElement(MissionCockpit, { view, width: 135, height: 19 }),
      React.createElement(EventLog, {
        events: [],
        width: 135,
        mode: 'conversation',
        showIdle: false,
      }),
      React.createElement(PromptBox, {
        edit: { value: '', cursor: 0 },
        width: 135,
        rowsBelow: 1,
      }),
      React.createElement(Footer, { width: 135 }),
    ),
    135,
  );
  // Debug rendering writes one frame at mount and one at unmount; measure the
  // final frame, which is what Ink compares against the terminal height.
  const finalFrame = output.slice(output.lastIndexOf('◉ argus'));
  assert.ok(finalFrame.trimEnd().split('\n').length < 19);
  assert.match(finalFrame, /MISSION/);
  assert.match(finalFrame, /AI RESEARCH TEAM/);
  assert.match(finalFrame, /MANAGER.*Waiting/);
  assert.match(finalFrame, /PLANNER.*Waiting/);
  assert.match(finalFrame, /ENGINEER.*Waiting/);
  assert.match(finalFrame, /REVIEWER.*Waiting/);
  assert.doesNotMatch(finalFrame, /All quiet|Standing watch|Waiting, unhurried/);
});

test('24-row operations panel stays below Ink full-screen clear threshold', async () => {
  const snap = {
    session: { id: 's-ops', display_name: '', objective: '', last_active: 0, cwd: '' },
    daemon: {
      alive: false, pid: null, uptime_seconds: null, backend: 'copilot', backend_label: 'Copilot',
      global_daily_cap_usd: 10_000,
      protocol: { name: 'argus.daemon', major: 1, minor: 1 },
    },
    roles: ['manager', 'planner', 'engineer', 'reviewer'].map((role) => ({
      role, backend: 'copilot', backend_label: 'Copilot', model: 'gpt-5.5',
      effort: 'xhigh', active: false, label: 'idle', status: 'idle', age_s: null,
    })),
    backlog: [], recent_events: [], spend_usd: 0, spend_status: 'priced',
    request_usage: {
      day: '2026-07-19',
      codex: { provider: 'codex', day: '2026-07-19', daily_calls: 0, daily_cap: 300, remaining: 300 },
      copilot: { provider: 'copilot', day: '2026-07-19', daily_calls: 476, daily_cap: 10_000, remaining: 9_524, premium_requests: 109, premium_cap: 10_000 },
    },
    cost_control: {
      active_reservations: 1, unresolved_calls: 108,
    },
    usage_summary: {
      input_tokens: 16_296, cached_input_tokens: 14_848, cache_write_tokens: 0,
      output_tokens: 103, reasoning_output_tokens: 61, call_count: 1,
    },
    mission_view: emptyMissionView(),
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

test('footer keeps backend and model details out of the main hierarchy', async () => {
  const output = await renderNode(
    React.createElement(Footer, { notice: '', width: 160 }),
    160,
  );
  assert.match(output, /Ctrl\+O operations/);
  assert.doesNotMatch(output, /Copilot|gpt-|pid/);
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
    status: 'running', priority: 10, iterate: true,
    iteration_cycles_done: 2, iteration_max_cycles: 6, iteration_cost_usd: 3.5,
  };
  const task = await renderPanel({ kind: 'task', data: item }, 60);
  assert.match(task, /Run five seeds/);
  assert.ok(task.split('\n').every((line) => Array.from(line).length <= 60));

  const snap = {
    session: { id: 's', display_name: '', objective: '', last_active: 0, cwd: '' },
    daemon: { alive: true, pid: 1, uptime_seconds: 1, backend: 'x', global_daily_cap_usd: 0 },
    roles: [], recent_events: [],
    backlog: [item, { ...item, id: 'done-1', title: 'Old result', status: 'done' }],
  } as Snapshot;
  const backlog = await renderPanel({ kind: 'backlog', selection: 0 }, 60, { snap });
  assert.match(backlog, /› running/);
  assert.doesNotMatch(backlog, /Old result/);
});
