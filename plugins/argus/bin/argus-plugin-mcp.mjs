#!/usr/bin/env node

import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

// The MCP server module. `argus_skill` is the package's pre-rename import
// name, still importable through its alias for one release.
const LEGACY_PACKAGE = 'argus_skill';
const MODULE_NAMES = ['argus.plugin.mcp_server', `${LEGACY_PACKAGE}.plugin.mcp_server`];

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
  for (const candidate of MODULE_NAMES) {
    const probe = spawnSync(
      command,
      [...prefix, '-c', `import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(${JSON.stringify(candidate)}) else 1)`],
      { stdio: 'ignore', windowsHide: true },
    );
    if (probe.status === 0) return candidate;
  }
  return null;
}

let selected = null;
for (const [command, prefix] of pythonCandidates()) {
  const moduleName = supportsArgus(command, prefix);
  if (moduleName) {
    selected = [command, prefix, moduleName];
    break;
  }
}
if (!selected) {
  console.error(
    'Argus Python package is unavailable. Install Argus and run argus doctor, '
    + 'or set ARGUS_PLUGIN_PYTHON to its Python interpreter.',
  );
  process.exit(127);
}

const [command, prefix, moduleName] = selected;
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
