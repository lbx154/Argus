#!/usr/bin/env node

import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const moduleName = 'argus.plugin.mcp_server';

function pythonCandidates() {
  const explicit = process.env.ARGUS_PLUGIN_PYTHON?.trim();
  if (explicit) return [[explicit, []]];

  const home = homedir();
  const argusHome = process.env.ARGUS_HOME?.trim() || (
    process.platform === 'win32'
      ? join(process.env.LOCALAPPDATA || join(home, 'AppData', 'Local'), 'Argus')
      : join(home, '.local', 'share', 'argus')
  );
  const managed = process.platform === 'win32'
    ? join(argusHome, 'venv', 'Scripts', 'python.exe')
    : join(argusHome, 'venv', 'bin', 'python');
  const candidates = existsSync(managed) ? [[managed, []]] : [];
  if (process.platform === 'win32') {
    candidates.push(['py', []], ['python', []]);
  } else {
    candidates.push(['python3', []], ['python', []]);
  }
  return candidates;
}

function supportsArgus(command, prefix) {
  const probe = spawnSync(
    command,
    [...prefix, '-c', 'import argus'],
    { stdio: 'ignore', windowsHide: true },
  );
  return probe.status === 0;
}

const selected = pythonCandidates().find(([command, prefix]) =>
  supportsArgus(command, prefix)
);
if (!selected) {
  console.error(
    'Argus Python package is unavailable. Install Argus and run argus doctor, '
    + 'or set ARGUS_PLUGIN_PYTHON to its Python interpreter.',
  );
  process.exit(127);
}

const [command, prefix] = selected;
if (process.env.ARGUS_PLUGIN_LAUNCHER_DRY_RUN === '1') {
  console.log(JSON.stringify({
    command,
    args: [...prefix, '-m', moduleName],
  }));
  process.exit(0);
}
const child = spawn(
  command,
  [...prefix, '-m', moduleName],
  { stdio: 'inherit', windowsHide: true },
);
const signalHandlers = new Map();
for (const signal of ['SIGINT', 'SIGTERM']) {
  const handler = () => child.kill(signal);
  signalHandlers.set(signal, handler);
  process.on(signal, handler);
}
child.once('error', (error) => {
  console.error(`Could not start Argus plugin server: ${error.message}`);
  process.exit(127);
});
child.once('exit', (code, signal) => {
  for (const [name, handler] of signalHandlers) {
    process.off(name, handler);
  }
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
