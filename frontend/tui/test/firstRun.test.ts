import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { test } from 'node:test';
import React from 'react';
import { render, type Instance } from 'ink';
import type { CreatedDaemon } from '../src/api.js';
import { FirstRun } from '../src/components/FirstRun.js';
import { initialProjectId, initialProjectSelection, interactiveStartup } from '../src/initialProject.js';

const ANSI = /\u001B\[[0-?]*[ -/]*[@-~]/g;
const words = (output: string) => output.replace(/[│╭╮╰╯─]/g, ' ').replace(/\s+/g, ' ');

interface Harness {
  stdin: PassThrough;
  instance: Instance;
  output: () => string;
}

function daemon(sid: string, spawned: boolean, objective = ''): CreatedDaemon {
  return {
    sid,
    rc: 0,
    spawned,
    objective,
    daemon: {
      alive: spawned,
      pid: spawned ? 42 : null,
      uptime_seconds: spawned ? 0 : null,
      backend: null,
      per_mission_cap_usd: null,
      daily_cap_usd: null,
      global_daily_cap_usd: null,
    },
  };
}

function mount(
  createDaemon: (objective: string) => Promise<CreatedDaemon>,
  onCreated: (created: CreatedDaemon) => void,
  width = 60,
): Harness {
  const stdout = new PassThrough() as PassThrough & {
    columns: number;
    rows: number;
    isTTY: boolean;
  };
  stdout.columns = width;
  stdout.rows = 24;
  stdout.isTTY = false;
  let output = '';
  stdout.on('data', (chunk) => { output += String(chunk); });

  const stdin = new PassThrough() as PassThrough & {
    isTTY: boolean;
    setRawMode: (enabled: boolean) => PassThrough;
    ref: () => PassThrough;
    unref: () => PassThrough;
  };
  stdin.isTTY = true;
  stdin.setRawMode = () => stdin;
  stdin.ref = () => stdin;
  stdin.unref = () => stdin;

  const instance = render(
    React.createElement(FirstRun, { createDaemon, onCreated }),
    {
      stdin: stdin as never,
      stdout: stdout as never,
      debug: true,
      exitOnCtrlC: false,
      patchConsole: false,
    },
  );
  return { stdin, instance, output: () => output.replace(ANSI, '') };
}

const settle = (ms = 25) => new Promise((resolve) => setTimeout(resolve, ms));

test('fresh Ink install selects the deliberate first-run state', () => {
  assert.equal(initialProjectId([]), null);
  assert.deepEqual(initialProjectSelection([], '  requested-session  '), {
    id: null,
    requested: 'requested-session',
    recovered: true,
  });
  const row = {
    id: 's-live', label: 'Live paper', objective: '', last_active: 2,
    daemon_alive: true, daemon_pid: 42, uptime_seconds: 3,
  };
  assert.equal(initialProjectId([row], 'missing'), 's-live');
  assert.deepEqual(initialProjectSelection([row], 's-live'), {
    id: 's-live', requested: 's-live', recovered: false,
  });
});

test('interactive launch is fresh by default and resumes only when explicit', () => {
  assert.deepEqual(interactiveStartup(), { kind: 'fresh' });
  assert.deepEqual(interactiveStartup('   '), { kind: 'fresh' });
  assert.deepEqual(interactiveStartup(' s-paper '), {
    kind: 'resume',
    project: 's-paper',
  });
});

test('first-run screen remains usable from 40 to 120 columns', async () => {
  for (const width of [40, 60, 120]) {
    const harness = mount(async () => daemon('s-idle', false), () => {}, width);
    await settle();
    const output = harness.output();
    const content = words(output);
    assert.match(content, /No daemons yet/);
    assert.match(content, /Objective \(optional\)/);
    assert.match(content, /create idle daemon/i);
    assert.ok(output.split('\n').every((line) => Array.from(line).length <= width));
    harness.instance.unmount();
  }
});

test('first-run objective creates and enters a started campaign', async () => {
  let requested = '';
  let entered: CreatedDaemon | null = null;
  const harness = mount(
    async (objective) => {
      requested = objective;
      return daemon('s-paper', true, objective);
    },
    (created) => { entered = created; },
  );
  await settle();
  harness.stdin.write('写一篇 AAAI 论文');
  await settle();
  assert.match(harness.output(), /写一篇 AAAI 论文/);
  harness.stdin.write('\r');
  await settle();
  assert.equal(requested, '写一篇 AAAI 论文');
  assert.equal(entered?.sid, 's-paper');
  assert.equal(entered?.spawned, true);
  harness.instance.unmount();
});

test('first-run blank objective creates an idle daemon', async () => {
  let requested = 'not-called';
  const harness = mount(
    async (objective) => {
      requested = objective;
      return daemon('s-idle', false);
    },
    () => {},
  );
  await settle();
  harness.stdin.write('\r');
  await settle();
  assert.equal(requested, '');
  harness.instance.unmount();
});

test('first-run creation failure stays actionable and can be retried', async () => {
  let attempts = 0;
  let entered = false;
  const harness = mount(
    async () => {
      attempts += 1;
      if (attempts === 1) throw new Error('token rejected');
      return daemon('s-retry', false);
    },
    () => { entered = true; },
  );
  await settle();
  harness.stdin.write('\r');
  await settle();
  assert.match(harness.output(), /Could not create daemon · token rejected · Enter to\s+.*retry/s);
  harness.stdin.write('\r');
  await settle();
  assert.equal(attempts, 2);
  assert.equal(entered, true);
  harness.instance.unmount();
});
