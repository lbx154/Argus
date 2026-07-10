#!/usr/bin/env node
// `argus` launcher — the terminal cockpit (Ink) for the argus-skill daemon.
// Uses the compiled build (dist/cli.js) ONLY when it is up-to-date; if any
// source file is newer than the build (or there is no build yet), it runs the
// TS source directly through tsx. This means editing src/ and relaunching
// `argus` always runs the latest code — no silent stale-dist trap (which once
// made a shipped streaming fix look absent because the CLI ran an old build).
import { existsSync, statSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const dist = join(root, 'dist', 'tui', 'src', 'cli.js');
const src = join(root, 'src', 'cli.tsx');
const shared = join(root, '..', 'core', 'src');

/** Newest mtime (ms) of any file under a directory tree, or 0 if absent. */
function newestMtime(dir) {
  let newest = 0;
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return 0;
  }
  for (const e of entries) {
    const p = join(dir, e.name);
    if (e.isDirectory()) newest = Math.max(newest, newestMtime(p));
    else {
      try {
        newest = Math.max(newest, statSync(p).mtimeMs);
      } catch {
        /* ignore a vanishing file */
      }
    }
  }
  return newest;
}

const distFresh =
  existsSync(dist) &&
  Math.max(newestMtime(join(root, 'src')), newestMtime(shared)) <= statSync(dist).mtimeMs;

if (distFresh) {
  await import(dist);
} else {
  // No build, or the source is newer than the build → run the current source
  // through tsx so `argus` never launches stale code.
  const { register } = await import('tsx/esm/api');
  register();
  await import(src);
}
