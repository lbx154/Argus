// HTML results are served unchanged on a separate origin. The cockpit alone grants an
// unguessable project/delivery hostname; this host has neither credentials nor API routes.

import { randomBytes } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { isAbsolute, posix, relative, resolve, sep } from 'node:path';
import { pipeline, Readable } from 'node:stream';
import { promisify } from 'node:util';
import { crc32, deflateRaw } from 'node:zlib';
import type { Project } from '../store.js';
import { sendFile, typeOf, under } from './files.js';
import { HttpError, hostPort, requestOrigin, type Context, type Handler } from './index.js';

const BUNDLE_BYTES = 2 ** 31;
const deflate = promisify(deflateRaw);

export function preview(project: (id: string) => Project, result: (work: Project, path: string, delivery?: string | null) => ReturnType<typeof under>, host: string, publicUrl?: string) {
  const source = publicUrl !== undefined ? URL.parse(publicUrl) : undefined;
  // Check the literal too: URL normalizes empty ?/#, encoded hosts and dot paths.
  const labels = source?.hostname.split('.') ?? [];
  if (source !== undefined && (!source || !/^https?:\/\/[a-z0-9*-]+(?:\.[a-z0-9-]+)+(?::[0-9]+)?\/?$/i.test(publicUrl!)
    || labels[0]!.split('*').length !== 2
    || labels.some((label, i) => {
      const filled = i ? label : label.replace('*', 'a'.repeat(32));
      return !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(filled) || filled.length > 63;
    }) || source.hostname.length + 31 > 253)) {
    throw new Error('--preview-url must be an HTTP(S) origin template with one * in its first DNS label, such as https://js-trial1-*.argusbot.cn');
  }
  const sites = new Map<string, { id: string; delivery: string | null; paths: Set<string> }>();
  const [before, after] = (source?.hostname ?? '*.localhost').split('*') as [string, string];
  // The entire template namespace is static-only, including unknown identifiers.
  const keyOf = (hostname: string): string | undefined => {
    const name = hostname.replace(/\.$/, '');
    return name.startsWith(before) && name.endsWith(after) && name.length >= before.length + after.length
      ? name.slice(before.length, name.length - after.length) : undefined;
  };
  const isHost = (hostname: string) => keyOf(hostname) !== undefined;
  let fallback: ReturnType<typeof createServer> | undefined;
  let fallbackPort: number | undefined;
  let listening: Promise<void> | undefined;
  const isOrigin = (origin: string) => {
    try {
      const url = new URL(origin);
      return !['http:', 'https:'].includes(url.protocol) || isHost(url.hostname)
        || !!fallbackPort && Number(url.port || (url.protocol === 'https:' ? 443 : 80)) === fallbackPort;
    } catch { return true; }
  };
  const handle = (request: IncomingMessage, response: ServerResponse, keyedPath = false) => {
    try {
      const incoming = requestOrigin(request);
      const url = new URL(request.url ?? '/', 'http://localhost');
      const [, pathKey, rest = ''] = /^\/([^/]+)(?:\/(.*))?$/.exec(url.pathname) ?? [];
      const key = keyedPath ? pathKey : keyOf(incoming.hostname);
      const path = keyedPath ? rest : url.pathname.slice(1);
      const site = key && sites.get(key);
      if (!site || !['GET', 'HEAD'].includes(request.method ?? '')) throw new HttpError(404, 'No preview here: preview links stop working when Argus restarts, so open the result again in Argus. This address only serves previews.');
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
      response.writeHead(error instanceof HttpError ? error.status : error instanceof URIError ? 400 : 500, { 'content-type': 'text/plain; charset=utf-8' })
        .end(error instanceof HttpError ? error.message : undefined);
    }
  };
  const html = ({ params: [id], url }: Context) => {
    const work = project(id!);
    const path = url.searchParams.get('path') ?? '';
    const delivery = url.searchParams.get('delivery');
    const found = result(work, path, delivery);
    if (!found || typeOf(found.file, found.path)[0] !== 'html') throw new HttpError(404, 'no such HTML result');
    return { work, path, delivery };
  };
  const routes: [string, RegExp, Handler][] = [
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/preview$/, async context => {
      const { work, path, delivery } = html(context);
      let key = [...sites].find(([, site]) => site.id === work.id && site.delivery === delivery)?.[0];
      if (!key) { key = randomBytes(16).toString('hex'); sites.set(key, { id: work.id, delivery, paths: new Set() }); }
      const incoming = requestOrigin(context.request);
      // Where *.localhost resolves: a browser on this machine. Another loopback address keeps the keyed port.
      const local = ['localhost', '127.0.0.1', '0.0.0.0', '[::]', '[::1]'].includes(incoming.hostname.replace(/\.$/, ''));
      const url = new URL(source?.origin ?? incoming.origin);
      if (source || local) {
        url.hostname = before + key + after;
        if (!source) url.protocol = 'http:';
      } else {
        // LAN/mobile browsers resolve localhost to their own machine. Preserve the older
        // keyed-path static listener there; its projects share storage, unlike subdomains.
        if (!listening) {
          const port = fallbackPort = context.request.socket.localPort! + 1;
          fallback = createServer((req, res) => handle(req, res, true));
          listening = new Promise<void>((accept, reject) => fallback!.once('error', reject).listen(port, host, accept)).catch((error: NodeJS.ErrnoException) => {
            listening = fallback = fallbackPort = undefined;
            throw new HttpError(503, `Preview server could not listen on ${hostPort(host, port)}: ${error.code ?? error.message}`, 'preview_unavailable');
          });
        }
        await listening;
        url.protocol = 'http:';
        url.port = String(fallbackPort);
      }
      const name = relative(work.workdir, resolve(work.workdir, path)).split(sep).join('/');
      url.pathname = `${source || local ? '' : '/' + key}/${name.split('/').map(encodeURIComponent).join('/')}`;
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
  return { routes, handle, isHost, isOrigin, close: async () => {
    if (!fallback) return;
    fallback.closeAllConnections();
    await new Promise<void>(accept => fallback!.close(() => accept()));
  } };
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
