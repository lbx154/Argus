import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, realpathSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, relative, resolve } from 'node:path';

const columns = 'run date parent model benchmark metric value baseline change gpu_h verdict path'.split(' ');
const header = columns.join('\t');
const verdicts = ['baseline', 'better', 'worse', 'same', 'broken'];
const [file, command, argument, model] = process.argv.slice(2);
const fail = message => { throw new Error(message); };
function read(path) {
  if (!existsSync(path)) return [];
  const lines = readFileSync(path, 'utf8').split(/\r?\n/);
  if (lines.at(-1) === '') lines.pop();
  if (lines.shift() !== header) fail(`${path}: line 1: invalid header`);
  const seen = new Set();
  return lines.flatMap((line, index) => {
    const cells = line.split('\t');
    if (cells.length !== columns.length || seen.has(cells[0])) {
      console.error(`${path}: line ${index + 2}: ${cells.length !== columns.length ? 'wrong column count' : 'duplicate run'}`);
      process.exitCode = 1;
      return [];
    }
    seen.add(cells[0]);
    return [Object.fromEntries(columns.map((key, i) => [key, cells[i]]))];
  });
}
const print = rows => console.log([header, ...rows.map(row => columns.map(key => row[key]).join('\t'))].join('\n'));
try {
  if (!file) fail('Usage: ledger.mjs <ledger.tsv> add <JSON> | show | near <benchmark> [model]');
  const rows = read(file);
  if (command === 'add') {
    if (process.exitCode) fail('Refusing to append to a damaged ledger');
    const row = JSON.parse(argument);
    const keys = columns.slice(2);
    if (!row || Array.isArray(row) || Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) fail(`Required keys: ${keys.join(', ')}`);
    for (const key of keys) {
      if (/[a-f0-9]{32,}/i.test(String(row[key]))) fail(`Hash-like value in ${key}`);
      if (['value', 'baseline', 'gpu_h'].includes(key)) {
        if (typeof row[key] !== 'number' || !Number.isFinite(row[key])) fail(`${key} must be a finite number`);
      } else {
        if (typeof row[key] !== 'string') fail(`${key} must be text`);
        row[key] = row[key].replace(/[\t\r\n]/g, ' ');
      }
    }
    if (!verdicts.includes(row.verdict)) fail('Invalid verdict');
    if (row.parent) {
      const match = /^(?:([^:/\\]+):)?(r[1-9]\d*)$/.exec(row.parent);
      if (!match || !(match[1] ? read(join(dirname(file), `${match[1]}.tsv`)) : rows).some(other => other.run === match[2])) fail('Parent not found');
      if (process.exitCode) fail('Damaged parent ledger');
    } else if (row.verdict !== 'baseline') fail('Only baseline may have an empty parent');
    if (!row.path || isAbsolute(row.path) || row.path.includes('\\') || !existsSync(row.path)) fail('Result path must exist in the workspace');
    const local = relative(realpathSync('.'), realpathSync(row.path));
    if (local === '..' || local.startsWith('../') || isAbsolute(local)) fail('Result path is outside the workspace');
    row.run = `r${rows.length + 1}`; row.date = new Date().toISOString().slice(0, 10);
    mkdirSync(dirname(file), { recursive: true });
    appendFileSync(file, `${existsSync(file) ? '' : `${header}\n`}${columns.map(key => row[key]).join('\t')}\n`);
  } else if (command === 'show') {
    const better = new Map(rows.filter(row => row.verdict === 'better').map(row => [row.benchmark, row]));
    print([...new Set([...rows.slice(-20), ...better.values()])]);
  } else if (command === 'near' && argument) {
    const others = readdirSync(dirname(file)).filter(name => name.endsWith('.tsv') && resolve(dirname(file), name) !== resolve(file)).sort();
    print(others.flatMap(name => {
      const matches = read(join(dirname(file), name)).filter(row => row.benchmark === argument && (!model || row.model === model));
      const better = matches.findLast(row => row.verdict === 'better');
      return matches.filter(row => ['baseline', 'broken'].includes(row.verdict) || row === better).map(row => ({ ...row, run: `${basename(name, '.tsv')}:${row.run}` }));
    }).slice(-30));
  } else fail('Unknown command or missing argument');
} catch (error) { console.error(error.message); process.exitCode = 1; }
