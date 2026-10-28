import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { mkdirSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { WAYS } from '../src/prompts.js';

// Each check of the research way, on a workspace that meets it and on one that does not.

/** The environment without git's, so git works on the workspace and nothing else. */
const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith('GIT_')));

const START = 1_700_000_000;

/** A workspace of these files, all last changed at START. */
async function workspace(t: TestContext, files: Record<string, string>): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'argus-way-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const [path, text] of Object.entries(files)) {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), text, 'latin1');
  }
  age(root, Object.fromEntries(Object.keys(files).map(path => [path, 0])));
  return root;
}

function check(name: string, root: string): { status: number | null; problems: string[] } {
  const run = spawnSync(process.execPath, [join(WAYS, 'research', 'checks', `${name}.mjs`), root], { encoding: 'utf8', env });
  return { status: run.status, problems: run.stdout.split('\n').filter(Boolean) };
}

function commit(root: string, ...paths: string[]): void {
  for (const args of [['init', '-q'], ['add', ...paths], ['commit', '-q', '-m', paths.join(' ')]]) {
    const run = spawnSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', ...args], { cwd: root, env, encoding: 'utf8' });
    assert.equal(run.status, 0, run.stderr);
  }
}

/** Set when files last changed, in seconds after START. */
function age(root: string, times: Record<string, number>): void {
  for (const [path, at] of Object.entries(times)) utimesSync(join(root, path), START + at, START + at);
}

const card = (statement: string, falsifier: string): string => `# Gated recall

${statement}

## Components

| Component | The idea prescribes | Notes |
|---|---|---|
| gated update | "a sigmoid gate decides what enters the cache" | \`src/model.py:GatedUpdate\` |
| recall cache | "keys are cached per layer" | \`src/model.py:RecallCache\` |

## Protocol

- Datasets: RULER, the released splits
- Baselines: KIVI, official code under \`third_party/kivi\`
${falsifier}`;

test('method-card: a card with its parts and tested components, committed before the code, holds', async t => {
  const root = await workspace(t, {
    'METHOD.md': card('Gated recall caches keys behind a learned gate.', '\n## What would falsify the claim\n\nNo gain over KIVI at 32k.\n'),
    'src/model.py': 'class GatedUpdate: pass\nclass RecallCache: pass\n',
    'tests/spec/test_knockouts.py': '@pytest.mark.component("gated update")\ndef a(): pass\n@pytest.mark.component(\'recall cache\')\ndef b(): pass\n',
  });
  commit(root, 'METHOD.md');
  commit(root, 'src', 'tests');
  assert.deepEqual(check('method-card', root), { status: 0, problems: [] });
});

test('method-card: names each part missing, each untested component, and code committed before the card', async t => {
  const root = await workspace(t, {
    'METHOD.md': card('<One paragraph: the method as the paper will claim it.>', ''),
    'src/model.py': 'class GatedUpdate: pass\n',
    'tests/spec/test_knockouts.py': '@pytest.mark.component("gated update")\ndef a(): pass\n',
  });
  commit(root, 'src');
  commit(root, 'METHOD.md', 'tests');
  const { status, problems } = check('method-card', root);
  assert.equal(status, 1);
  assert.deepEqual(problems.slice(0, 3), [
    'METHOD.md does not say what would falsify the claim',
    'METHOD.md still holds a template placeholder: <One paragraph: the method as the paper will claim it.>',
    'component "recall cache" has no test under tests/spec marked component("recall cache")',
  ]);
  assert.match(problems[3] ?? '', /^src\/model\.py was committed \([0-9a-f]+\) before METHOD\.md: the method was built before it was stated$/);
  assert.equal(problems.length, 4);
});

const mainTex = '\\documentclass{article}\n\\graphicspath{{figures/}}\n\\begin{document}\n\\input{sections/results}\n\\bibliography{refs}\n\\end{document}\n';
const attention = '@inproceedings{vaswani2017attention,\n  title = {Attention Is All You Need},\n  url = {https://arxiv.org/abs/1706.03762},\n}\n';

test('paper: a current build whose citations and figures can be traced holds', async t => {
  const root = await workspace(t, {
    'paper/main.tex': mainTex,
    'paper/sections/results.tex': 'Gated recall keeps accuracy \\citep{vaswani2017attention}. % \\cite{dropped}\n\\includegraphics[width=\\linewidth]{accuracy}\n',
    'paper/refs.bib': attention,
    'paper/figures/accuracy.pdf': '%PDF-1.4\n1 0 obj << /Type /Font /Subtype /TrueType >> endobj\n',
    'paper/figures/accuracy.source.json': '{"source": "paper/plot.py", "data": ["results"]}',
    'paper/plot.py': 'plot the results\n',
    'results/seed1.json': '{}',
    'paper/main.pdf': '%PDF-1.4\n',
    'paper/main.log': 'Output written on main.pdf (8 pages).\n',
  });
  age(root, { 'paper/figures/accuracy.pdf': 10, 'paper/main.pdf': 20 });
  assert.deepEqual(check('paper', root), { status: 0, problems: [] });
});

test('paper: names each citation, figure and build that cannot be traced or is out of date', async t => {
  const root = await workspace(t, {
    'paper/main.tex': mainTex,
    'paper/sections/results.tex': '\\citep{vaswani2017attention,missing2024} \\cite{noid2020}\n\\includegraphics{accuracy}\n\\includegraphics{plot.png}\n\\includegraphics{gone}\n',
    'paper/refs.bib': `${attention}@article{noid2020,\n  title = {Remembered, not looked up},\n}\n`,
    'paper/figures/accuracy.pdf': '%PDF-1.4\n1 0 obj << /Type /Font /Subtype /Type3 >> endobj\n',
    'paper/figures/accuracy.source.json': '{"source": "paper/plot.py", "data": ["results"]}',
    'paper/figures/plot.png': '\x89PNG\r\n\x1a\n\0\0\0\x19tEXtSoftware\0Matplotlib version3.8.0\0\0\0\0IDAT',
    'paper/plot.py': 'plot the results\n',
    'results/seed1.json': '{}',
    'paper/main.pdf': '%PDF-1.4\n',
    'paper/main.log': 'LaTeX Warning: There were undefined references.\n',
  });
  age(root, { 'paper/figures/accuracy.pdf': 10, 'paper/figures/plot.png': 10, 'results/seed1.json': 15, 'paper/main.pdf': 20, 'paper/sections/results.tex': 30 });
  assert.deepEqual(check('paper', root), { status: 1, problems: [
    '\\cite{missing2024} has no entry in the bibliography',
    'the bibliography entry noid2020 has no doi, url, eprint or isbn to open it by',
    'the figure gone is included but missing',
    'paper/figures/accuracy.pdf is older than results: draw it again',
    'paper/figures/accuracy.pdf embeds Type 3 fonts: export it with TrueType fonts (matplotlib: pdf.fonttype 42)',
    'paper/figures/plot.png has no record of what made it: paper/figures/plot.source.json',
    'paper/figures/plot.png is a matplotlib raster export: include the vector PDF',
    'paper/main.pdf is older than paper/sections/results.tex: build it again',
    'paper/main.log reports undefined citations or references',
  ] });
});
