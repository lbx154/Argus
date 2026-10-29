// An HTML result in the cockpit: the page with the local files it names inlined, served to a
// sandboxed frame under a policy that lets it reach nothing, this server least of all; or the page
// and those files as they are, in a zip. Results chosen together download as one zip too.

import { readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import type { ServerResponse } from 'node:http';
import { posix } from 'node:path';
import { pipeline, Readable } from 'node:stream';
import { promisify } from 'node:util';
import { crc32, deflateRaw } from 'node:zlib';
import type { Project } from '../store.js';
import { typeOf, under } from './files.js';
import { HttpError, type Context, type Handler } from './index.js';

const PAGE_BYTES = 12 << 20;
const BUNDLE_BYTES = 2 ** 31;
/** The page's own scripts and styles run; nothing in it may call out, send a form or move its base. */
const POLICY = "default-src 'none'; script-src 'unsafe-inline' data: blob:; style-src 'unsafe-inline' data:; "
  + "img-src data: blob:; font-src data:; media-src data: blob:; base-uri 'none'; form-action 'none'";
const deflate = promisify(deflateRaw);

/** `grant` lets in the preview page, which a frame loads by URL and so without the bearer. */
export function preview(project: (id: string) => Project, grant: (response: ServerResponse) => void): [string, RegExp, Handler][] {
  const html = ({ params: [id], url }: Context) => {
    const { workdir } = project(id!);
    const found = under(workdir, url.searchParams.get('path') ?? '');
    if (!found || typeOf(found.path)[0] !== 'html') throw new HttpError(404, 'no such HTML result');
    if (found.stat.size > PAGE_BYTES) throw new HttpError(413, 'This page is too large to preview; download it instead.');
    return { found, ...page(workdir, found.path, readFileSync(found.file, 'utf8')) };
  };
  return [
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/preview$/, context => {
      const { html: shown, warnings, files } = html(context);
      grant(context.response);
      return { html: shown, warnings, file_count: 1 + files.length, served_page: true };
    }],
    // Sandboxed by its policy, the page is no page of this origin, whichever frame holds it.
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/preview\/page$/, context => {
      const shown = html(context).html;
      context.response.writeHead(200, {
        'content-type': 'text/html; charset=utf-8', 'content-security-policy': `sandbox allow-scripts allow-downloads; ${POLICY}`,
        'x-content-type-options': 'nosniff', 'cache-control': 'private, no-store', 'referrer-policy': 'no-referrer',
      }).end(shown);
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/bundle$/, context => {
      const { found, warnings, files } = html(context);
      const all = [found, ...files];
      // The page's folder is the zip's root, or the folder above it that holds every file the page names.
      let root = posix.dirname(found.path);
      while (root !== '.' && !all.every(file => file.path.startsWith(`${root}/`))) root = posix.dirname(root);
      const entries = new Map<string, string | Buffer>(all.map(file => [root === '.' ? file.path : file.path.slice(root.length + 1), file.file]));
      if (warnings.length) entries.set('ARGUS-PREVIEW-NOTES.txt', Buffer.from(warnings.join('\n')));
      sendZip(context.response, 'argus-website.zip', entries);
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/artifacts\/bundle$/, async ({ params: [id], body, response }) => {
      const { paths, name, summary } = await body();
      if (!Array.isArray(paths) || !paths.length) throw new HttpError(400, 'no results named');
      const { workdir } = project(id!);
      const entries = new Map<string, string | Buffer>();
      const missing: string[] = [];
      let bytes = 0;
      for (const path of new Set(paths.map(path => String(path).trim()).filter(Boolean))) {
        const found = under(workdir, path);
        if (!found) missing.push(path);
        else if ((bytes += found.stat.size) > BUNDLE_BYTES) throw new HttpError(413, 'These results are too large for one archive; download them one by one.');
        else entries.set(found.path, found.file);
      }
      if (!entries.size) throw new HttpError(404, 'None of these results are available any more.');
      let notes = String(summary ?? '').trim();
      if (missing.length) notes += `${notes ? '\n\n' : ''}Not included (no longer available):\n${missing.map(path => `- ${path}`).join('\n')}`;
      if (notes) entries.set('README-Argus.md', Buffer.from(`${notes}\n`));
      sendZip(response, zipName(String(name ?? '')), entries);
    }],
  ];
}

/** The page with each local file it names (a src or poster, a link's href, a CSS url()) inlined as
 * a data: URL, a stylesheet's own url()s from where it is, and the files so inlined. A file outside
 * the workdir, hidden, missing or past the size limit is left out, and named in a warning. */
function page(workdir: string, path: string, html: string) {
  const warnings: string[] = [];
  const files: NonNullable<ReturnType<typeof under>>[] = [];
  const inlined = new Map<string, string>();
  let bytes = Buffer.byteLength(html);
  const inline = (ref: string, from: string): string => {
    if (!ref || /^(?:[a-z][\w+.-]*:|\/\/|#)/i.test(ref)) return ref;
    let name = ref.split(/[?#]/)[0]!;
    try { name = decodeURIComponent(name); } catch { /* as written */ }
    // A path from the root is from the page's folder, the site's root.
    const file = posix.join(posix.dirname(from), name);
    if (!inlined.has(file)) {
      // Taken before it is read, so a stylesheet that leads back to itself gets nothing.
      inlined.set(file, 'data:,');
      const found = under(workdir, file);
      if (!found || (bytes += found.stat.size) > PAGE_BYTES) warnings.push(`Could not include local resource: ${ref}`);
      else {
        files.push(found);
        const data = found.path.endsWith('.css') ? Buffer.from(css(readFileSync(found.file, 'utf8'), found.path)) : readFileSync(found.file);
        inlined.set(file, `data:${typeOf(found.path)[1].split(';')[0]};base64,${data.toString('base64')}`);
      }
    }
    return inlined.get(file)!;
  };
  const css = (text: string, from: string): string =>
    text.replace(/url\(\s*(['"]?)([^'")]*)\1\s*\)/gi, (_, _quote: string, ref: string) => `url(${inline(ref.trim(), from)})`);
  const inlinedPage = html
    .replace(/(<style\b[^>]*>)([\s\S]*?)(<\/style>)/gi, (_, open: string, sheet: string, close: string) => open + css(sheet, path) + close)
    .replace(/<[a-z][^>]*>/gi, tag => tag.replace(/(\s(src|poster|href|style)\s*=\s*)(["'])(.*?)\3/gi,
      (attribute, head: string, name: string, quote: string, value: string) => {
        const key = name.toLowerCase();
        if (key === 'href' && !/^<link\b/i.test(tag)) return attribute;
        return head + quote + (key === 'style' ? css(value, path) : inline(value, path)) + quote;
      }));
  return { html: inlinedPage.replace(/^(\s*<!doctype[^>]*>)?/i, `$1<meta http-equiv="Content-Security-Policy" content="${POLICY}">`), warnings, files };
}

/** A zip's name from the one asked for: no character a file system refuses, at most 80 of them. */
const zipName = (name: string): string =>
  ([...name.replace(/[\\/:*?"<>|\x00-\x1f]+/g, ' ').replace(/^[ .]+|[ .]+$/g, '').trim().replace(/\s+/g, ' ')].slice(0, 80).join('')
    || 'argus-results') + '.zip';

/** The entries, each a file to read or its bytes, as a zip made while it is sent. A reader who
 * leaves, or a file gone meanwhile, ends it where it is. */
function sendZip(response: ServerResponse, name: string, entries: Map<string, string | Buffer>): void {
  response.writeHead(200, {
    'content-type': 'application/zip', 'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(name)}`,
    'x-content-type-options': 'nosniff', 'cache-control': 'private, no-store',
  });
  pipeline(Readable.from(zip(entries), { objectMode: false }), response, () => {});
}

/** Little-endian fields, each a value and its width in bytes. */
const fields = (...values: [number, number][]): Buffer =>
  Buffer.concat(values.map(([value, width]) => { const field = Buffer.alloc(width); field.writeUIntLE(value, 0, width); return field; }));

/** A plain zip, one file in memory at a time, each deflated off the event loop under its UTF-8
 * name and dated now. Neither a file nor the whole may pass 4 GiB; the size limits keep it within. */
async function* zip(entries: Map<string, string | Buffer>): AsyncGenerator<Buffer> {
  const now = new Date();
  const time = now.getHours() << 11 | now.getMinutes() << 5 | now.getSeconds() >> 1;
  const date = now.getFullYear() - 1980 << 9 | now.getMonth() + 1 << 5 | now.getDate();
  const directory: Buffer[] = [];
  let offset = 0;
  for (const [name, content] of entries) {
    const data = typeof content === 'string' ? await readFile(content) : content;
    const packed = await deflate(data);
    const path = Buffer.from(name);
    // What a file's entry in the directory repeats of its header: zip 2.0, a UTF-8 name, deflated.
    const head = fields([20, 2], [0x800, 2], [8, 2], [time, 2], [date, 2], [crc32(data), 4], [packed.length, 4], [data.length, 4], [path.length, 2], [0, 2]);
    directory.push(fields([0x02014b50, 4], [20, 2]), head, fields([0, 2], [0, 2], [0, 2], [0, 4], [offset, 4]), path);
    const local = Buffer.concat([fields([0x04034b50, 4]), head, path]);
    offset += local.length + packed.length;
    yield local;
    yield packed;
  }
  const listing = Buffer.concat(directory);
  yield listing;
  yield fields([0x06054b50, 4], [0, 2], [0, 2], [entries.size, 2], [entries.size, 2], [listing.length, 4], [offset, 4], [0, 2]);
}
