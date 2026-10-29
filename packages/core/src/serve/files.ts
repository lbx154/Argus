// Files over HTTP: the web cockpit's own build, and a project's results. A result is a file of
// the workdir the work created or changed; nothing is read out of what a role wrote in prose. A
// result the Manager delivers is kept as it was then, its text files copied into the project's
// delivered/, so that what a later delivery changed can be shown.

import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { createReadStream, mkdirSync, readdirSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync, type Stats } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { basename, extname, isAbsolute, join, posix, relative, resolve, sep } from 'node:path';
import type { ArtifactInfo, ArtifactKind, DeliverySnapshot } from '@argus/contracts';
import type { Project } from '../store.js';

const TYPES: Record<string, [ArtifactKind, string]> = {
  '.html': ['html', 'text/html; charset=utf-8'], '.htm': ['html', 'text/html; charset=utf-8'],
  '.js': ['text', 'text/javascript; charset=utf-8'], '.mjs': ['text', 'text/javascript; charset=utf-8'],
  '.css': ['text', 'text/css; charset=utf-8'], '.md': ['markdown', 'text/markdown; charset=utf-8'],
  '.json': ['json', 'application/json'], '.webmanifest': ['json', 'application/manifest+json'],
  '.csv': ['table', 'text/csv; charset=utf-8'], '.tsv': ['table', 'text/tab-separated-values; charset=utf-8'],
  '.svg': ['image', 'image/svg+xml'], '.png': ['image', 'image/png'], '.jpg': ['image', 'image/jpeg'], '.jpeg': ['image', 'image/jpeg'],
  '.gif': ['image', 'image/gif'], '.webp': ['image', 'image/webp'], '.ico': ['image', 'image/x-icon'],
  '.pdf': ['pdf', 'application/pdf'], '.mp3': ['audio', 'audio/mpeg'], '.wav': ['audio', 'audio/wav'],
  '.mp4': ['video', 'video/mp4'], '.webm': ['video', 'video/webm'],
  // A robot or a mesh, which the cockpit opens in 3D; the files it names are read by modelAssets.
  '.urdf': ['model', 'application/xml'], '.stl': ['model', 'model/stl'], '.obj': ['model', 'model/obj'],
  '.dae': ['model', 'model/vnd.collada+xml'], '.glb': ['model', 'model/gltf-binary'], '.gltf': ['model', 'model/gltf+json'],
  '.woff2': ['binary', 'font/woff2'], '.woff': ['binary', 'font/woff'], '.ttf': ['binary', 'font/ttf'], '.wasm': ['binary', 'application/wasm'],
};
const TEXT = new Set(['.txt', '.log', '.py', '.ts', '.tsx', '.sh', '.yaml', '.yml', '.toml', '.tex', '.bib', '.ipynb', '.r', '.sql', '.xml']);
const PREVIEW_BYTES = 128 * 1024;
const LISTED = 200;
/** The most a text result holds to be edited in the page, or copied as delivered. */
export const TEXT_LIMIT = 512 * 1024;
/** The copies kept of each delivered file, the newest. */
const COPIES = 10;

export function typeOf(path: string): [ArtifactKind, string] {
  const ext = extname(path).toLowerCase();
  return TYPES[ext] ?? (TEXT.has(ext) ? ['text', 'text/plain; charset=utf-8'] : ['binary', 'application/octet-stream']);
}

/** The file, or the part a `Range: bytes=` header asks for (PDF.js reads by ranges). */
export function sendFile(request: IncomingMessage, response: ServerResponse, file: string, size: number, headers: Record<string, string>): void {
  const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.range ?? '');
  const partial = Boolean(range?.[1] || range?.[2]);
  let [start, end] = [0, size - 1];
  if (range && partial) {
    start = range[1] ? Number(range[1]) : Math.max(0, size - Number(range[2]));
    if (range[1] && range[2]) end = Math.min(Number(range[2]), size - 1);
    if (start > end) {
      response.writeHead(416, { 'content-range': `bytes */${size}` }).end();
      return;
    }
  }
  response.writeHead(partial ? 206 : 200, {
    'content-type': typeOf(file)[1], ...headers, 'accept-ranges': 'bytes', 'content-length': String(Math.max(0, end - start + 1)),
    ...(partial ? { 'content-range': `bytes ${start}-${end}/${size}` } : {}),
  });
  if (request.method === 'HEAD' || size === 0) response.end();
  else createReadStream(file, { start, end }).pipe(response);
}

/** A file under `root` by its path there; null outside it, hidden, or not a file. */
export function under(root: string, path: string): { file: string; path: string; stat: Stats } | null {
  let file: string;
  try {
    // The names the file system keeps: on Windows a hidden file's short name (HIDDEN~1) is not hidden.
    root = realpathSync.native(root);
    file = realpathSync.native(resolve(root, path));
  } catch { return null; }
  const inside = relative(root, file);
  if (!inside || inside.startsWith('..') || isAbsolute(inside) || inside.split(sep).some(part => part.startsWith('.'))) return null;
  const stat = statSync(file);
  return stat.isFile() ? { file, path: inside.split(sep).join('/'), stat } : null;
}

/** A file's text; null when it cannot be read, is not UTF-8, or holds a NUL, as binary files do. */
export function textOf(file: string): string | null {
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(readFileSync(file));
    return text.includes('\0') ? null : text;
  } catch { return null; }
}

/** The copy kept of a delivered file by its version; null when there is none. */
export const kept = (dir: string, version: string): string | null =>
  /^[0-9a-f]{32}$/.test(version) ? textOf(join(dir, 'delivered', version)) : null;

/** Each file of a result the Manager delivers, as it is now. A text file is copied into `dir`'s
 * delivered/ under a new version, unless it is the same as its last copy, whose version it keeps;
 * past ten copies of a file, the oldest is deleted. A file too large or not text is named by its
 * size and time and not copied; a path that names no result is left out. */
export function keep(project: Project, dir: string, paths: string[]): DeliverySnapshot[] {
  return paths.flatMap((path): DeliverySnapshot[] => {
    try {
      const found = under(project.workdir, path);
      if (!found) return [];
      const { size, mtimeMs } = found.stat;
      const text = size <= TEXT_LIMIT ? textOf(found.file) : null;
      if (text === null) return [{ path, version: `${size}:${mtimeMs}`, size, stored: false }];
      const versions = (project.delivered ??= {})[path] ??= [];
      let version = versions.at(-1);
      if (version === undefined || kept(dir, version) !== text) {
        version = randomUUID().replaceAll('-', '');
        mkdirSync(join(dir, 'delivered'), { recursive: true });
        writeFileSync(join(dir, 'delivered', version), text);
        versions.push(version);
        for (const old of versions.splice(0, versions.length - COPIES)) rmSync(join(dir, 'delivered', old), { force: true });
      }
      return [{ path, version, size, stored: true }];
    } catch { return []; }
  });
}

/** A result's name: its main file's own title, a Markdown heading or an HTML title, else the file's name. */
export function titleOf(project: Project, path: string): string {
  const found = under(project.workdir, path);
  const text = found && found.stat.size <= TEXT_LIMIT ? textOf(found.file) ?? '' : '';
  const own = /\.(md|markdown)$/i.test(path) ? /^#{1,6}\s+(.+)$/m.exec(text)?.[1]
    : /\.html?$/i.test(path) ? /<title[^>]*>([^<]*)<\/title>/i.exec(text)?.[1] : undefined;
  return own?.trim() || basename(path);
}

export function artifactInfo(project: Project, found: { file: string; path: string; stat: Stats }, preview = false): ArtifactInfo {
  const [kind, mime] = typeOf(found.path);
  const info: ArtifactInfo = {
    path: found.path, name: basename(found.path), why: '', exists: true, kind, mime,
    size: found.stat.size, mtime: found.stat.mtimeMs / 1000, storage_path: join(project.workdir, found.path),
  };
  if (!preview || !['text', 'markdown', 'json', 'table', 'html'].includes(kind)) return info;
  const text = readFileSync(found.file).subarray(0, PREVIEW_BYTES).toString('utf8');
  return { ...info, preview: text, truncated: found.stat.size > PREVIEW_BYTES };
}

/** Git run outside the sandbox on a repository the roles write: it changes nothing, and runs
 * none of the programs the repository's own config or attributes name (an fsmonitor, a filter, a
 * diff driver, a signature check), nor goes into a submodule, whose config is the roles' too. */
export const SAFE_GIT = ['--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'log.showSignature=false'];

/** The files under `root` changed since `since` (epoch ms), newest first: the LISTED newest, six
 * folders deep, and none hidden, in node_modules or __pycache__, or ignored by the git repository
 * `root` is in, as what a build or a cache writes is. */
export function changed(root: string, since = 0): { file: string; path: string; stat: Stats }[] {
  const found: { file: string; path: string; stat: Stats }[] = [];
  const walk = (dir: string, depth: number): void => {
    for (const entry of readdirSync(join(root, dir), { withFileTypes: true })) {
      if (entry.name.startsWith('.') || entry.name === 'node_modules' || entry.name === '__pycache__') continue;
      const path = dir ? `${dir}/${entry.name}` : entry.name;
      if (entry.isDirectory() && depth < 6) walk(path, depth + 1);
      else if (entry.isFile()) {
        const file = join(root, path);
        const stat = statSync(file);
        if (stat.mtimeMs >= since) found.push({ file, path, stat });
      }
    }
  };
  walk('', 0);
  const ignored = new Set(spawnSync('git', [...SAFE_GIT, 'check-ignore', '--stdin', '-z'], {
    cwd: root, input: found.map(({ path }) => path).join('\0'), encoding: 'utf8',
  }).stdout?.split('\0'));
  return found.filter(({ path }) => !ignored.has(path)).sort((a, b) => b.stat.mtimeMs - a.stat.mtimeMs).slice(0, LISTED);
}

/** What the project delivered, then the workdir's other files it created or changed since it
 * began, newest first. */
export const artifacts = (project: Project): ArtifactInfo[] => {
  const delivered = Object.keys(project.delivered ?? {});
  return [
    ...delivered.flatMap(path => under(project.workdir, path) ?? []),
    ...changed(project.workdir, Date.parse(project.created)).filter(({ path }) => !delivered.includes(path)),
  ].map(found => artifactInfo(project, found));
};

/** Where a file a model names may be, from the model's folder: a relative path from there; a ROS
 * `package://name/rest` from the nearest enclosing folder named `name`, then `name/rest` or `rest`
 * beside the model or above it. Nothing outside the workdir is found by `under`. */
export function modelAssets(model: string, ref: string): string[] {
  const dirs: string[] = [];
  for (let dir = posix.dirname(model); ; dir = posix.dirname(dir)) {
    dirs.push(dir);
    if (dir === '.' || dir === '/') break;
  }
  const pkg = /^package:\/\/([^/]+)\/(.+)$/.exec(ref);
  if (!pkg) return /^(?:[a-z][\w+.-]*:|\/|~)/i.test(ref) ? [] : [posix.join(dirs[0]!, ref)];
  const [, name, rest] = pkg;
  return [
    ...dirs.filter(dir => posix.basename(dir) === name).map(dir => posix.join(dir, rest!)),
    ...dirs.map(dir => posix.join(dir, name!, rest!)), ...dirs.map(dir => posix.join(dir, rest!)),
  ];
}

/** How a result is sent: never as a page of this origin, which holds the cockpit's token. */
export function rawHeaders(path: string, download: boolean): Record<string, string> {
  const [kind, mime] = typeOf(path);
  // XML, an SVG or a URDF among them, may carry a script as a page does.
  const active = kind === 'html' || mime.includes('xml');
  return {
    'content-type': active ? 'text/plain; charset=utf-8' : mime, 'x-content-type-options': 'nosniff',
    ...(download ? { 'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(basename(path))}` } : {}),
  };
}

/** The built cockpit: the shell is never cached, the fingerprinted assets always are. */
export function sendStatic(request: IncomingMessage, response: ServerResponse, web: string, pathname: string): boolean {
  const found = under(web, pathname === '/' ? 'index.html' : `.${pathname}`);
  if (!found) return false;
  const cache = found.path === 'index.html' ? 'no-store' : found.path.startsWith('assets/') ? 'public, max-age=31536000, immutable' : '';
  sendFile(request, response, found.file, found.stat.size, cache ? { 'cache-control': cache } : {});
  return true;
}
