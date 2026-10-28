// Files over HTTP: the web cockpit's own build, and a project's results. A result is a file of
// the workdir the work created or changed; nothing is read out of what a role wrote in prose.

import { createReadStream, readdirSync, readFileSync, realpathSync, statSync, type Stats } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { basename, extname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import type { ArtifactInfo, ArtifactKind } from '@argus/contracts';
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
  '.mp4': ['video', 'video/mp4'], '.webm': ['video', 'video/webm'], '.glb': ['model', 'model/gltf-binary'], '.stl': ['model', 'model/stl'],
  '.woff2': ['binary', 'font/woff2'], '.woff': ['binary', 'font/woff'], '.ttf': ['binary', 'font/ttf'], '.wasm': ['binary', 'application/wasm'],
};
const TEXT = new Set(['.txt', '.log', '.py', '.ts', '.tsx', '.sh', '.yaml', '.yml', '.toml', '.tex', '.bib', '.ipynb', '.r', '.sql', '.xml']);
const PREVIEW_BYTES = 128 * 1024;
const LISTED = 200;

function typeOf(path: string): [ArtifactKind, string] {
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

/** The workdir's files created or changed since the project began, newest first. */
export function artifacts(project: Project): ArtifactInfo[] {
  const since = Date.parse(project.created);
  const found: ArtifactInfo[] = [];
  const walk = (dir: string, depth: number): void => {
    for (const entry of readdirSync(join(project.workdir, dir), { withFileTypes: true })) {
      if (found.length >= LISTED || entry.name.startsWith('.') || entry.name === 'node_modules' || entry.name === '__pycache__') continue;
      const path = dir ? `${dir}/${entry.name}` : entry.name;
      if (entry.isDirectory() && depth < 6) walk(path, depth + 1);
      else if (entry.isFile()) {
        const stat = statSync(join(project.workdir, path));
        if (stat.mtimeMs >= since) found.push(artifactInfo(project, { file: join(project.workdir, path), path, stat }));
      }
    }
  };
  walk('', 0);
  return found.sort((a, b) => (b.mtime ?? 0) - (a.mtime ?? 0));
}

/** How a result is sent: never as a page of this origin, which holds the cockpit's token. */
export function rawHeaders(path: string, download: boolean): Record<string, string> {
  const [kind, mime] = typeOf(path);
  const active = kind === 'html' || mime === 'image/svg+xml';
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
