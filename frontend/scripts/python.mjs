#!/usr/bin/env node
// Run the project's Python interpreter from an npm script.
//
// The frontend build scripts call `argus.release_tools.*` before compiling.
// A bare `python` on PATH is often not the interpreter that has Argus
// installed, and the resulting ModuleNotFoundError says nothing about what to
// do. This launcher picks the first interpreter that can import Argus, in this
// order: ARGUS_PYTHON, the checkout's own .venv, then python3 / python on PATH.
// Every candidate is run with the checkout on PYTHONPATH, so a source checkout
// works without an editable install.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const WINDOWS = process.platform === 'win32';

function withCheckoutOnPath(env) {
  const current = env.PYTHONPATH ? env.PYTHONPATH.split(path.delimiter) : [];
  const merged = [ROOT, ...current.filter((entry) => entry && entry !== ROOT)];
  return { ...env, PYTHONPATH: merged.join(path.delimiter) };
}

function candidates() {
  const out = [];
  if (process.env.ARGUS_PYTHON) out.push(process.env.ARGUS_PYTHON);
  for (const venv of ['.venv', 'venv']) {
    const bin = WINDOWS
      ? path.join(ROOT, venv, 'Scripts', 'python.exe')
      : path.join(ROOT, venv, 'bin', 'python');
    if (existsSync(bin)) out.push(bin);
  }
  out.push('python3', 'python');
  if (WINDOWS) out.push('py');
  return [...new Set(out)];
}

// Probe the module the command is about to run (`-m argus.x.y`), so an
// interpreter that can import the package but lacks that module's
// dependencies is skipped instead of failing later with a bare traceback.
function probeModule(args) {
  const index = args.indexOf('-m');
  const named = index >= 0 ? args[index + 1] : undefined;
  return named && named.startsWith('argus') ? named : 'argus';
}

function importsArgus(interpreter, env, moduleName) {
  const probe = spawnSync(
    interpreter,
    ['-c', `import ${moduleName}`],
    { env, stdio: 'ignore', windowsHide: true },
  );
  return probe.status === 0;
}

const env = withCheckoutOnPath(process.env);
const moduleName = probeModule(process.argv.slice(2));
const interpreter = candidates().find((candidate) => importsArgus(candidate, env, moduleName));
if (!interpreter) {
  process.stderr.write(
    `frontend/scripts/python.mjs: no Python interpreter on this machine can import ${moduleName}. `
    + 'Activate the project environment (or set ARGUS_PYTHON to its python) '
    + 'and run the build again.\n',
  );
  process.exit(1);
}

const result = spawnSync(interpreter, process.argv.slice(2), {
  env,
  stdio: 'inherit',
  windowsHide: true,
});
if (result.error) {
  process.stderr.write(`frontend/scripts/python.mjs: ${result.error.message}\n`);
  process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
