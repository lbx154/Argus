// HTML results are served unchanged on a separate origin. The cockpit alone grants an
// unguessable project/delivery path; this listener has neither credentials nor API routes.

import { randomBytes } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createServer, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { isAbsolute, posix, relative, resolve, sep } from 'node:path';
import { pipeline, Readable } from 'node:stream';
import { promisify } from 'node:util';
import { crc32, deflateRaw } from 'node:zlib';
import type { Project } from '../store.js';
import { sendFile, typeOf, under } from './files.js';
import { HttpError, requestOrigin, type Context, type Handler } from './index.js';

const BUNDLE_BYTES = 2 ** 31;
const deflate = promisify(deflateRaw);

export async function preview(project: (id: string) => Project, result: (work: Project, path: string, delivery?: string | null) => ReturnType<typeof under>, host: string, listenPort: number, publicUrl?: string) {
  const source = publicUrl ? new URL(publicUrl) : undefined;
  if (source && (!['http:', 'https:'].includes(source.protocol) || source.username || source.password
    || source.pathname !== '/' || source.search || source.hash)) throw new Error('--preview-url must be an HTTP(S) origin, such as https://preview.example.com');
  const sites = new Map<string, { id: string; delivery: string | null; paths: Set<string> }>();
  const server = createServer((request, response) => {
    try {
      const url = new URL(request.url ?? '/', 'http://localhost');
      const [, key, path = ''] = /^\/([^/]+)(?:\/(.*))?$/.exec(url.pathname) ?? [];
      const site = key && sites.get(key);
      if (!site || !['GET', 'HEAD'].includes(request.method ?? '')) throw new HttpError(404, 'Not Found');
      const name = decodeURIComponent(path);
      const work = project(site.id);
      const directory = url.pathname.endsWith('/');
      const found = result(work, directory ? name + 'index.html' : name, site.delivery);
      if (!found && !directory && result(work, name ? name + '/index.html' : 'index.html', site.delivery)) {
        response.writeHead(301, {
          location: url.pathname + '/' + url.search, 'cache-control': 'no-store', 'referrer-policy': 'no-referrer',
        }).end();
        return;
      }
      if (!found) throw new HttpError(404, 'Not Found');
      sendFile(request, response, found.file, found.stat.size, {
        'content-type': typeOf(found.file, found.path)[1], 'x-content-type-options': 'nosniff',
        'cache-control': 'no-store', 'referrer-policy': 'no-referrer',
      });
      if (request.method === 'GET') response.once('finish', () => {
        if (response.statusCode === 200 || response.statusCode === 206) site.paths.add(found.path);
      });
    } catch (error) {
      response.writeHead(error instanceof HttpError ? error.status : error instanceof URIError ? 400 : 500).end();
    }
  });
  await new Promise<void>((resolve, reject) => server.once('error', reject).listen(listenPort, host, resolve))
    .catch((error: unknown) => { throw new Error(`Preview server could not listen on ${host}:${listenPort}: ${error instanceof Error ? error.message : String(error)}`); });
  const port = (server.address() as AddressInfo).port;
  const close = async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  };
  const html = ({ params: [id], url }: Context) => {
    const work = project(id!);
    const path = url.searchParams.get('path') ?? '';
    const delivery = url.searchParams.get('delivery');
    const found = result(work, path, delivery);
    if (!found || typeOf(found.file, found.path)[0] !== 'html') throw new HttpError(404, 'no such HTML result');
    return { work, path, delivery, found };
  };
  const routes: [string, RegExp, Handler][] = [
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/preview$/, context => {
      const { work, path, delivery } = html(context);
      let key = [...sites].find(([, site]) => site.id === work.id && site.delivery === delivery)?.[0];
      if (!key) { key = randomBytes(24).toString('base64url'); sites.set(key, { id: work.id, delivery, paths: new Set() }); }
      const url = new URL(source?.origin ?? requestOrigin(context.request));
      if (!source) url.port = String(port);
      url.pathname = `/${key}/${path.split('/').map(encodeURIComponent).join('/')}`;
      return { url: url.href };
    }],
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/bundle$/, context => {
      const { work, path: requested, delivery } = html(context);
      const path = relative(work.workdir, resolve(work.workdir, requested)).split(sep).join('/');
      if (path === '..' || path.startsWith('../') || isAbsolute(path)) throw new HttpError(404, 'no such HTML result');
      // Include the page and files this project/delivery's preview actually served.
      let root = posix.dirname(path);
      const entries = new Map<string, string | Buffer>();
      let bytes = 0;
      const add = (name: string) => {
        const file = result(work, name, delivery);
        if (!file || entries.has(name)) return;
        if ((bytes += file.stat.size) > BUNDLE_BYTES) throw new HttpError(413, 'This website is too large for one archive.', 'archive_too_large');
        entries.set(name, file.file);
        while (root !== '.' && !name.startsWith(`${root}/`)) root = posix.dirname(root);
      };
      // A kept page remains downloadable even if its original folder has since disappeared.
      add(path);
      const site = [...sites.values()].find(site => site.id === work.id && site.delivery === delivery);
      for (const name of site?.paths ?? []) add(name);
      sendZip(context.response, 'argus-website.zip', new Map([...entries].map(([name, file]) => [posix.relative(root, name), file])));
    }],
    ['POST', /^\/api\/projects\/([^/]+)\/artifacts\/bundle$/, async ({ params: [id], body, response }) => {
      const { paths, name, summary, delivery } = await body();
      if (delivery != null && typeof delivery !== 'string') throw new HttpError(400, 'invalid delivery');
      if (!Array.isArray(paths) || !paths.length) throw new HttpError(400, 'no results named', 'input_required');
      const work = project(id!);
      const entries = new Map<string, string | Buffer>();
      const missing: string[] = [];
      let bytes = 0;
      for (const path of new Set(paths.map(path => String(path).trim()).filter(Boolean))) {
        const found = result(work, path, delivery);
        if (!found) missing.push(path);
        else if ((bytes += found.stat.size) > BUNDLE_BYTES) throw new HttpError(413, 'These results are too large for one archive; download them one by one.', 'archive_too_large');
        else entries.set(found.path, found.file);
      }
      if (!entries.size) throw new HttpError(404, 'None of these results are available any more.');
      let notes = String(summary ?? '').trim();
      if (missing.length) notes += `${notes ? '\n\n' : ''}Not included (no longer available):\n${missing.map(path => `- ${path}`).join('\n')}`;
      if (notes) entries.set('README-Argus.md', Buffer.from(`${notes}\n`));
      sendZip(response, zipName(String(name ?? '')), entries);
    }],
  ];
  return { routes, close, port, origin: source?.origin };
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
