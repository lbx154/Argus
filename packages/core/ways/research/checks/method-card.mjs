#!/usr/bin/env node
// The method card, METHOD.md, against the work built on it. Checks that
// - METHOD.md has its parts: a statement under the title, a Components table with a row per
//   component, a Protocol and what would falsify the claim, with no template placeholder left;
// - every component has a test under tests/spec marked with its name, component("<name>");
// - METHOD.md was committed no later than the first method code (code outside tests/,
//   third_party/ and paper/), so the method was stated before it was built.
// Run: node checks/method-card.mjs [workspace]   (default: the current directory)
// Exit: 0 all hold; 1 something does not, one line per problem; 2 the check could not run.

import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

const root = resolve(process.argv[2] ?? '.');
const problems = [];

/** The card's component names, after noting what the card lacks; null without a card. */
function card() {
  const path = join(root, 'METHOD.md');
  if (!existsSync(path)) {
    problems.push('METHOD.md is missing: write it from the selected route before the method code');
    return null;
  }
  const text = readFileSync(path, 'utf8').replace(/<!--[\s\S]*?-->/g, '');
  const [head, ...parts] = text.split(/^## /m);
  const section = name => parts.find(part => part.split('\n')[0].toLowerCase().includes(name))?.split('\n').slice(1).join('\n').trim();
  if (!head.split('\n').filter(line => !line.startsWith('# ')).join('').trim()) problems.push('METHOD.md has no statement of the method under its title');
  if (!section('protocol')) problems.push('METHOD.md has no Protocol');
  if (!section('falsif')) problems.push('METHOD.md does not say what would falsify the claim');
  const placeholder = /(^|[|:#]\s*)<[A-Za-z][^<>]*\s[^<>]*>/m.exec(text);
  if (placeholder) problems.push(`METHOD.md still holds a template placeholder: ${placeholder[0].replace(/^[|:#\s]+/, '').split('\n')[0]}`);
  const rows = (section('components') ?? '').split('\n').map(line => line.trim())
    .filter(line => line.startsWith('|') && !/^\|[\s:|-]+$/.test(line)).slice(1);
  const names = rows.map(row => row.split('|')[1].replace(/[`*]/g, '').trim()).filter(Boolean);
  if (!names.length) problems.push('METHOD.md has no Components table with a row per component');
  return names;
}

function tested(names) {
  const spec = join(root, 'tests', 'spec');
  const files = existsSync(spec) ? readdirSync(spec, { recursive: true, encoding: 'utf8' }).map(file => join(spec, file)) : [];
  const text = files.filter(file => statSync(file).isFile()).map(file => readFileSync(file, 'utf8')).join('\n');
  for (const name of names) {
    if (!text.includes(`component("${name}"`) && !text.includes(`component('${name}'`)) {
      problems.push(`component "${name}" has no test under tests/spec marked component("${name}")`);
    }
  }
}

function order() {
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith('GIT_')));
  const log = spawnSync('git', ['-C', root, 'log', '--reverse', '--relative', '--diff-filter=A', '--name-only', '--format=%x00%h'], { encoding: 'utf8', env });
  if (log.status !== 0) {
    problems.push('the workspace has no git history: commit METHOD.md, then the method code');
    return;
  }
  const code = file => /\.(py|ipynb|js|mjs|ts|c|cc|cpp|cu|h|hpp|rs|go|jl|java|scala|r)$/i.test(file) && !/^(tests?|third_party|paper)\//.test(file);
  for (const commit of log.stdout.split('\0').slice(1)) {
    const [hash, ...files] = commit.trim().split('\n');
    if (files.includes('METHOD.md')) return;
    const first = files.find(code);
    if (first) {
      problems.push(`${first} was committed (${hash}) before METHOD.md: the method was built before it was stated`);
      return;
    }
  }
  problems.push('METHOD.md is not committed');
}

try {
  if (!statSync(root).isDirectory()) throw new Error(`${root} is not a directory`);
  const names = card();
  if (names) {
    tested(names);
    order();
  }
} catch (error) {
  console.log(`method-card: could not run: ${error.message}`);
  process.exit(2);
}
for (const problem of problems) console.log(problem);
process.exit(problems.length ? 1 : 0);
