#!/usr/bin/env node
// Argus's TypeScript core, packages/core of the repository, against the rules a script can see.
// Reports
// - an exported function, constant or class in src/ that no other file uses: dead code, or an
//   export nobody needs;
// - a way of working named in src/ or prompts/ ('research', ways/research): the core branching
//   on a way, when what a way needs belongs in its folder;
// - a timer in src/ (setTimeout, setInterval, AbortSignal.timeout): Argus waiting, polling or
//   timing out what the backend runs;
// - in src/, prompts/, ways/ and scripts/, a path of one machine (/home/<user>, /Users/<user>,
//   C:\Users\<user>), a literal digest or a computed hash: a digest is a second copy of a fact,
//   so Argus keeps and compares the fact itself.
// A finding is a candidate: fix it, or say in the letter why it stays.
// Run: node checks/core.mjs [repository]   (default: the current directory)
// Exit: 0 nothing found; 1 findings, one line each (file:line: what); 2 the check could not run.

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';

const repo = resolve(process.argv[2] ?? '.');
const core = join(repo, 'packages', 'core');
const findings = [];

/** The files under the core's `dir` whose names match `pattern`, each with its path and text. */
const files = (dir, pattern = /\.(ts|mjs|js|md|sh)$/) => existsSync(join(core, dir))
  ? readdirSync(join(core, dir), { recursive: true, encoding: 'utf8' }).sort().map(path => join(core, dir, path))
    .filter(path => pattern.test(path) && statSync(path).isFile()).map(path => ({ path, text: readFileSync(path, 'utf8') }))
  : [];
const shown = file => relative(repo, file.path).split(sep).join('/');
/** A finding on each line of `file` that `pattern` matches, saying `what` of the match. */
function lines(file, pattern, what) {
  file.text.split('\n').forEach((line, i) => {
    const match = pattern.exec(line);
    if (match) findings.push(`${shown(file)}:${i + 1}: ${what(match[0])}`);
  });
}

try {
  if (!statSync(join(core, 'src')).isDirectory()) throw new Error(`${core} has no src/`);
  const src = files('src', /\.ts$/);
  const users = [...src, ...files('test', /\.ts$/), ...files('scripts')];
  for (const file of src) {
    for (const { 1: name, index } of file.text.matchAll(/^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|const|let|class)\s+([\w$]+)/gm)) {
      const word = new RegExp(`(?<![\\w$])${name.replace(/\$/g, '\\$')}(?![\\w$])`);
      if (!users.some(other => other !== file && word.test(other.text))) {
        findings.push(`${shown(file)}:${file.text.slice(0, index).split('\n').length}: ${name} is exported, but no other file uses it`);
      }
    }
    lines(file, /\b(setTimeout|setInterval|AbortSignal\.timeout)\s*\(/, found => `${found.replace(/\s*\($/, '')}: Argus waits, polls or times out; the backend does that`);
  }
  const wayRoot = join(core, 'ways');
  const ways = existsSync(wayRoot) ? readdirSync(wayRoot).filter(name => existsSync(join(wayRoot, name, 'README.md'))) : [];
  if (ways.length) {
    const named = new RegExp(`(['"\`/])(${ways.join('|')})(['"\`/])`);
    for (const file of [...src, ...files('prompts')]) lines(file, named, found => `names the way ${found.slice(1, -1)}: what a way needs belongs in its folder`);
  }
  for (const file of [...src, ...files('prompts'), ...files('ways'), ...files('scripts')]) {
    lines(file, /\/home\/[\w.-]+|\/Users\/[\w.-]+|[A-Za-z]:\\Users\\[\w.-]+/, found => `${found} is a path of one machine`);
    lines(file, /(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,}(?![0-9A-Fa-f])|\bcreateHash\s*\(|\bsubtle\.digest\s*\(/, () => 'a digest: keep and compare the thing itself');
  }
} catch (error) {
  console.log(`core: could not run: ${error.message}`);
  process.exit(2);
}
for (const finding of findings) console.log(finding);
process.exit(findings.length ? 1 : 0);
