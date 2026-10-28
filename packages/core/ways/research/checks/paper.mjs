#!/usr/bin/env node
// The paper as built, paper/main.tex and paper/main.pdf, and what it rests on. Checks that
// - paper/main.pdf is newer than every source it shows (the .tex files main.tex reaches, the
//   bibliography, the included figures), and its build log, paper/main.log, names no undefined
//   citation or reference;
// - every \cite key is an entry of the bibliography, and every cited entry carries a doi, url,
//   eprint or isbn, so a reader can open what it cites;
// - every included figure exists and has its record beside it, <stem>.source.json:
//   {"source": the script or editable drawing it is made from, "data": [the files or folders it
//   plots]}, paths relative to the workspace, and the figure is newer than each of them;
// - no included PDF embeds a Type 3 font and no included PNG is a matplotlib raster export: a
//   data figure is a vector PDF with TrueType fonts.
// Run: node checks/paper.mjs [workspace]   (default: the current directory)
// Exit: 0 all hold; 1 something does not, one line per problem; 2 the check could not run.

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative, resolve } from 'node:path';

const root = resolve(process.argv[2] ?? '.');
const paper = join(root, 'paper');
const problems = [];
const shown = path => relative(root, path);
const isFile = path => existsSync(path) && statSync(path).isFile();
/** When a file, or the newest file in a folder, last changed. */
const changed = path => statSync(path).isDirectory()
  ? Math.max(0, ...readdirSync(path, { recursive: true, encoding: 'utf8' }).map(file => join(path, file)).filter(isFile).map(changed))
  : statSync(path).mtimeMs;

/** The .tex files main.tex reaches through \input, \include and \subfile, comments removed. */
function sources() {
  const texts = new Map();
  const visit = name => {
    const path = resolve(paper, extname(name) ? name : `${name}.tex`);
    if (texts.has(path) || !isFile(path)) return;
    const text = readFileSync(path, 'utf8').replace(/(?<!\\)%.*/g, '');
    texts.set(path, text);
    for (const [, next] of text.matchAll(/\\(?:input|include|subfile)\s*\{([^}]+)\}/g)) visit(next.trim());
  };
  visit('main.tex');
  return texts;
}

function citations(tex, shows) {
  const entries = new Map();
  for (const [, names] of tex.matchAll(/\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}/g)) {
    for (const name of names.split(',').map(name => name.trim())) {
      const bib = resolve(paper, name.endsWith('.bib') ? name : `${name}.bib`);
      if (!isFile(bib)) {
        problems.push(`the bibliography ${shown(bib)} is missing`);
        continue;
      }
      shows.push(bib);
      const text = readFileSync(bib, 'utf8');
      const starts = [...text.matchAll(/@(\w+)\s*\{\s*([^,\s]+)\s*,/g)].filter(([, type]) => !/^(string|comment|preamble)$/i.test(type));
      starts.forEach((start, i) => entries.set(start[2], text.slice(start.index, starts[i + 1]?.index)));
    }
  }
  const cited = [...tex.matchAll(/\\(\w*cite\w*)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]+)\}/g)]
    .filter(([, command]) => command !== 'citestyle').flatMap(([, , keys]) => keys.split(',').map(key => key.trim()));
  for (const key of new Set(cited)) {
    if (key === '*') continue; // \nocite{*}
    const entry = entries.get(key);
    if (entry === undefined) problems.push(`\\cite{${key}} has no entry in the bibliography`);
    else if (!/[\s,{](doi|url|eprint|isbn)\s*=|\\url\{/i.test(entry)) problems.push(`the bibliography entry ${key} has no doi, url, eprint or isbn to open it by`);
  }
}

function figures(tex, shows) {
  const dirs = ['', ...[.../\\graphicspath\s*\{((?:\s*\{[^}]*\})+)\s*\}/.exec(tex)?.[1].matchAll(/\{([^}]*)\}/g) ?? []].map(([, dir]) => dir)];
  const included = new Set();
  for (const [, name] of tex.matchAll(/\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}/g)) {
    const figure = dirs.flatMap(dir => ['', '.pdf', '.png', '.jpg', '.jpeg', '.eps'].map(ext => resolve(paper, dir, name.trim() + ext))).find(isFile);
    if (figure) included.add(figure);
    else problems.push(`the figure ${name.trim()} is included but missing`);
  }
  for (const figure of included) {
    shows.push(figure);
    const record = `${figure.replace(/\.[^./]*$/, '')}.source.json`;
    if (!isFile(record)) problems.push(`${shown(figure)} has no record of what made it: ${shown(record)}`);
    else {
      let made;
      try { made = JSON.parse(readFileSync(record, 'utf8')); } catch { made = null; }
      if (typeof made?.source !== 'string') problems.push(`${shown(record)} does not name the figure's source`);
      for (const from of [made?.source, ...[made?.data ?? []].flat()].filter(path => typeof path === 'string')) {
        const path = resolve(root, from);
        if (!existsSync(path)) problems.push(`${shown(record)} names ${from}, which is missing`);
        else if (changed(path) > changed(figure)) problems.push(`${shown(figure)} is older than ${from}: draw it again`);
      }
    }
    const bytes = readFileSync(figure).toString('latin1');
    if (/\/Subtype\s*\/Type3\b/.test(bytes)) problems.push(`${shown(figure)} embeds Type 3 fonts: export it with TrueType fonts (matplotlib: pdf.fonttype 42)`);
    if (/Software\0matplotlib/i.test(bytes.split('IDAT')[0])) problems.push(`${shown(figure)} is a matplotlib raster export: include the vector PDF`);
  }
}

try {
  if (!statSync(root).isDirectory()) throw new Error(`${root} is not a directory`);
  const texts = sources();
  if (!texts.size) problems.push('paper/main.tex is missing');
  else {
    const tex = [...texts.values()].join('\n');
    const shows = [...texts.keys()];
    citations(tex, shows);
    figures(tex, shows);
    const pdf = join(paper, 'main.pdf');
    const newer = isFile(pdf) && shows.filter(path => changed(path) > changed(pdf)).map(shown);
    if (!newer) problems.push('paper/main.pdf is missing: build it with latexmk -cd -pdf paper/main.tex');
    else if (newer.length) problems.push(`paper/main.pdf is older than ${newer.join(', ')}: build it again`);
    const log = join(paper, 'main.log');
    if (isFile(log) && /There were undefined (references|citations)/.test(readFileSync(log, 'latin1'))) {
      problems.push('paper/main.log reports undefined citations or references');
    }
  }
} catch (error) {
  console.log(`paper: could not run: ${error.message}`);
  process.exit(2);
}
for (const problem of problems) console.log(problem);
process.exit(problems.length ? 1 : 0);
