import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { test } from 'node:test';
import React from 'react';
import { render, type Instance } from 'ink';
import type { CreatedDaemon } from '../src/api.js';
import { FirstRun } from '../src/components/FirstRun.js';
import {
  initialProjectId,
  initialProjectSelection,
  interactiveStartup,
  liveProjectsForLaunchCwd,
} from '../src/initialProject.js';
import { projectsForLaunchCwd } from '../../core/src/projects.js';

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

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
  assert.deepEqual(interactiveStartup(undefined, true), { kind: 'pick' });
  assert.deepEqual(interactiveStartup(' s-paper '), {
    kind: 'resume',
    project: 's-paper',
  });
});

test('resume scope excludes unassigned legacy Web sessions unless --all is used', () => {
  const rows = [
    { id: 'local', label: 'local', objective: '', launch_cwd: '/work/repo', last_active: 3, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
    { id: 'other', label: 'other', objective: '', launch_cwd: '/other', last_active: 2, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
    { id: 'legacy', label: 'legacy', objective: '', cwd: '/home/me/.argus-skill/projects/legacy', last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
  ];
  assert.deepEqual(projectsForLaunchCwd(rows, '/work', false).map((row) => row.id), ['local']);
  assert.equal(projectsForLaunchCwd(rows, '/work', true).length, 3);
});

test('resume scope normalizes Windows separators, drive case, and filesystem root', () => {
  const windowsRows = [
    { id: 'local', label: 'local', objective: '', launch_cwd: 'C:\\Work\\Repo', last_active: 2, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
    { id: 'other', label: 'other', objective: '', launch_cwd: 'D:\\Work', last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
  ];
  assert.deepEqual(
    projectsForLaunchCwd(windowsRows, 'c:/work', false).map((row) => row.id),
    ['local'],
  );

  const posixRows = [
    { id: 'root-child', label: 'root-child', objective: '', launch_cwd: '/work/repo', last_active: 1, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
  ];
  assert.deepEqual(projectsForLaunchCwd(posixRows, '/', false).map((row) => row.id), ['root-child']);
});

test('plain startup reuses the highest-ranked live Windows session for its launch directory', () => {
  const rows = [
    { id: 'stopped', label: 'stopped', objective: '', launch_cwd: 'C:\\Work\\Repo', last_active: 99, daemon_alive: false, daemon_pid: null, uptime_seconds: null },
    { id: 'older-live', label: 'older', objective: '', launch_cwd: 'c:/work/repo/sub', last_active: 2, daemon_alive: true, daemon_pid: 41, uptime_seconds: 10 },
    { id: 'newer-live', label: 'newer', objective: '', launch_cwd: 'C:\\WORK\\REPO', last_active: 8, daemon_alive: true, daemon_pid: 42, uptime_seconds: 20 },
    { id: 'other-live', label: 'other', objective: '', launch_cwd: 'D:\\Work\\Repo', last_active: 10, daemon_alive: true, daemon_pid: 43, uptime_seconds: 30 },
  ];
  assert.deepEqual(
    liveProjectsForLaunchCwd(rows, 'c:/work/repo').map((row) => row.id),
    ['newer-live', 'older-live'],
  );
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

test('first-run exit during creation ignores the late UI result', async () => {
  const creation = deferred<CreatedDaemon>();
  let entered = false;
  const harness = mount(
    async () => creation.promise,
    () => { entered = true; },
  );
  await settle();
  harness.stdin.write('\r');
  await settle();
  harness.stdin.write('\u0003');
  await Promise.race([
    harness.instance.waitUntilExit(),
    new Promise((_, reject) => setTimeout(() => reject(new Error('FirstRun did not exit')), 500)),
  ]);

  creation.resolve(daemon('s-late', true));
  await settle();
  assert.equal(entered, false);
});
