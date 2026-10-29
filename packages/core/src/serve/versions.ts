// A result's versions. The operator's editions of a text result are saved beside it and never
// over it, appended to the project's editions.jsonl;
// the first is saved with what it was made from, as version 0. And what changed: from a text result
// as it is to an edition of it, saved or not, or in a file between two of its deliveries, read from
// the copies kept of it as delivered (`keep`).

import { appendFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Project, Store } from '../store.js';
import { kept, TEXT_LIMIT, textOf, under } from './files.js';
import { HttpError, type Handler } from './index.js';
import { jsonLines } from './views.js';

interface Edition { kind: string; source: string; version: number; content: string; note: string; created: number }

const EDITIONS = /^\/api\/projects\/([^/]+)\/editions\/artifact$/;
const DIFF_CHARS = 128 * 1024;
/** The unchanged lines shown around each change. */
const AROUND = 3;
/** The most pairs of lines a change is matched over, line by line. */
const MATCHED = 1 << 24;

export function versions(store: Store, project: (id: string) => Project): [string, RegExp, Handler][] {
  /** The text of a result as it is: a text file of the workdir, small enough to edit in the page. */
  const original = (work: Project, path: string): string => {
    const found = under(work.workdir, path);
    if (!found) throw new HttpError(404, 'no such result');
    if (found.stat.size > TEXT_LIMIT) throw new HttpError(413, 'This document is too large for the inline editor; download it to edit.', 'editor_too_large');
    const text = textOf(found.file);
    if (text === null) throw new HttpError(422, 'Only text results can be edited; other files can still be revised in conversation.', 'text_only');
    return text;
  };
  const editions = (id: string, source: string): Edition[] =>
    (jsonLines(join(store.dir(id), 'editions.jsonl')).rows as unknown as Edition[]).filter(row => row.kind === 'artifact' && row.source === source);
  return [
    ['GET', EDITIONS, ({ params: [id], url }) => {
      const work = project(id!);
      const source = url.searchParams.get('source') ?? '';
      return { original: original(work, source), versions: editions(work.id, source) };
    }],
    ['POST', EDITIONS, async ({ params: [id], body }) => {
      const work = project(id!);
      const { source, content, note = '', base_version: base } = await body();
      if (typeof source !== 'string' || !source || typeof content !== 'string') throw new HttpError(400, 'an edition is the content of a source', 'invalid_request');
      const first = original(work, source);
      // A save names the version it follows, and is refused after one it never saw.
      const latest = editions(work.id, source).at(-1)?.version ?? 0;
      if (base !== latest) throw new HttpError(409, 'A newer version exists. Reload versions before saving.', 'version_conflict');
      const line = (version: number, content: string, note: string): string =>
        `${JSON.stringify({ kind: 'artifact', source, version, content, note, created: Date.now() / 1000 })}\n`;
      appendFileSync(join(store.dir(work.id), 'editions.jsonl'), (latest ? '' : line(0, first, '')) + line(latest + 1, content, String(note)));
      return { version: latest + 1, content };
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/editions\/artifact\/diff$/, async ({ params: [id], body }) => {
      const { source, content } = await body();
      if (typeof source !== 'string' || typeof content !== 'string') throw new HttpError(400, 'a diff is from a source to a content', 'invalid_request');
      const diff = unified(source, original(project(id!), source), content);
      return { diff: diff.slice(0, DIFF_CHARS), truncated: diff.length > DIFF_CHARS };
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/delivery-diff$/, ({ params: [id], url }) => {
      const dir = store.dir(project(id!).id);
      const path = url.searchParams.get('path') ?? '';
      const before = kept(dir, url.searchParams.get('before') ?? '');
      const after = kept(dir, url.searchParams.get('after') ?? '');
      const available = before !== null && after !== null;
      const diff = available ? unified(path, before, after) : '';
      return { path, available, diff: diff.slice(0, DIFF_CHARS), truncated: diff.length > DIFF_CHARS };
    }],
  ];
}

/** What changed from `before` to `after`, as a unified diff of their lines. The lines both begin
 * and end with are set aside; those between are matched by the most they share in order, or, past
 * MATCHED pairs, read as all removed, then all added. */
export function unified(path: string, before: string, after: string): string {
  const [a, b] = [before, after].map(text => text.replace(/\n$/, '').split('\n')) as [string[], string[]];
  let head = 0;
  let tail = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const x = a.slice(head, a.length - tail);
  const y = b.slice(head, b.length - tail);
  // Every line in order, marked ' ' when both have it, '-' when only `before` does, '+' when only `after` does.
  const rows = a.slice(0, head).map(line => ` ${line}`);
  if (x.length * y.length <= MATCHED) {
    // shared[i * w + j]: how many lines x from i on and y from j on share, in order.
    const w = y.length + 1;
    const shared = new Uint16Array((x.length + 1) * w);
    for (let i = x.length - 1; i >= 0; i--) {
      for (let j = y.length - 1; j >= 0; j--) {
        shared[i * w + j] = x[i] === y[j] ? shared[(i + 1) * w + j + 1]! + 1 : Math.max(shared[(i + 1) * w + j]!, shared[i * w + j + 1]!);
      }
    }
    for (let i = 0, j = 0; i < x.length || j < y.length;) {
      if (i < x.length && j < y.length && x[i] === y[j]) { rows.push(` ${x[i++]}`); j++; }
      else if (j === y.length || (i < x.length && shared[(i + 1) * w + j]! >= shared[i * w + j + 1]!)) rows.push(`-${x[i++]}`);
      else rows.push(`+${y[j++]}`);
    }
  } else for (const [mark, lines] of [['-', x], ['+', y]] as const) for (const line of lines) rows.push(mark + line);
  for (const line of a.slice(a.length - tail)) rows.push(` ${line}`);

  // Changes closer than twice AROUND share a hunk, headed by where it starts in each text and how long it is there.
  const changes = rows.flatMap((row, index) => row[0] === ' ' ? [] : [index]);
  const range = (start: number, count: number): string => `${count ? start : start - 1},${count}`;
  let diff = changes.length ? `--- a/${path}\n+++ b/${path}\n` : '';
  let [row, old, now] = [0, 1, 1];
  for (let first = 0; first < changes.length;) {
    let last = first;
    while (last + 1 < changes.length && changes[last + 1]! - changes[last]! <= 2 * AROUND + 1) last++;
    const from = Math.max(0, changes[first]! - AROUND);
    for (; row < from; row++) {
      if (rows[row]![0] !== '+') old++;
      if (rows[row]![0] !== '-') now++;
    }
    const hunk = rows.slice(from, changes[last]! + AROUND + 1);
    const count = (skip: string): number => hunk.filter(line => line[0] !== skip).length;
    diff += `@@ -${range(old, count('+'))} +${range(now, count('-'))} @@\n${hunk.join('\n')}\n`;
    first = last + 1;
  }
  return diff;
}
