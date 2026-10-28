#!/usr/bin/env node
// A way of working, the folder Argus lists for the Manager, against what makes it usable. Checks that
// - README.md's first line is a heading of one short sentence (under 150 characters): it is all
//   the Manager sees when it chooses a way;
// - every skills/, checks/ or examples/ path that README.md or a skill names exists;
// - README.md names every file in skills/ and checks/, so a reader learns when to open or run it;
// - every file in checks/ is a Node script (.mjs) that parses, whose opening comment says how to
//   run it (Run:) and what its exit codes mean (Exit:);
// - examples/ holds a file: the real case that shows the way helps.
// Run: node checks/way.mjs [folder]   (default: the current directory)
// Exit: 0 all hold; 1 something does not, one line per problem; 2 the check could not run.

import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';

const root = resolve(process.argv[2] ?? '.');
const problems = [];
/** The files in the way's folder `dir`, by path within the way. */
const files = dir => existsSync(join(root, dir))
  ? readdirSync(join(root, dir)).sort().map(name => `${dir}/${name}`).filter(path => statSync(join(root, path)).isFile())
  : [];

try {
  if (!statSync(root).isDirectory()) throw new Error(`${root} is not a directory`);
  const readme = existsSync(join(root, 'README.md')) ? readFileSync(join(root, 'README.md'), 'utf8') : null;
  if (readme === null) problems.push('README.md is missing');
  else {
    const first = readme.split('\n')[0];
    if (!/^# \S/.test(first)) problems.push('README.md does not begin with a heading that says what the way is for');
    else if (first.length > 150) problems.push(`README.md's first line is ${first.length} characters: say what the way is for in one short sentence`);
  }
  const skills = files('skills');
  for (const path of ['README.md', ...skills.filter(path => path.endsWith('.md'))]) {
    const text = existsSync(join(root, path)) ? readFileSync(join(root, path), 'utf8') : '';
    for (const [named] of text.matchAll(/(?<![\w./-])(?:skills|checks|examples)\/[\w.-]+(?:\/[\w.-]+)*/g)) {
      const target = named.replace(/\.+$/, '');
      if (!existsSync(join(root, target))) problems.push(`${path} names ${target}, which is missing`);
    }
  }
  const checks = files('checks');
  for (const path of [...skills, ...checks]) {
    if (readme !== null && !readme.includes(path) && !readme.includes(`\`${basename(path)}\``)) {
      problems.push(`README.md does not name ${path}: say when to ${path.startsWith('skills/') ? 'open' : 'run'} it`);
    }
  }
  for (const path of checks) {
    if (!path.endsWith('.mjs')) {
      problems.push(`${path} is not a Node script (.mjs)`);
      continue;
    }
    const parsed = spawnSync(process.execPath, ['--check', join(root, path)], { encoding: 'utf8' });
    if (parsed.status !== 0) problems.push(`${path} does not parse: ${parsed.stderr.split('\n').find(line => /Error/.test(line)) ?? 'node --check failed'}`);
    const opening = readFileSync(join(root, path), 'utf8').split('\n').filter(line => !line.startsWith('#!'));
    const comment = opening.slice(0, opening.findIndex(line => !line.startsWith('//'))).join('\n');
    if (!/\bRun:/.test(comment) || !/\bExit:/.test(comment)) problems.push(`${path} does not open with a comment saying how to run it (Run:) and what its exit codes mean (Exit:)`);
  }
  if (!existsSync(join(root, 'examples')) || !readdirSync(join(root, 'examples'), { recursive: true }).length) {
    problems.push('examples/ is empty: add the real case that shows the way helps');
  }
} catch (error) {
  console.log(`way: could not run: ${error.message}`);
  process.exit(2);
}
for (const problem of problems) console.log(problem);
process.exit(problems.length ? 1 : 0);
