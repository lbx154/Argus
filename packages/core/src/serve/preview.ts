// An HTML result in the cockpit: the page with the local files it names inlined, for the page's
// sandboxed frame, under a policy that lets it reach nothing, this server least of all. Results
// are not packed into a zip here; each file downloads by itself.

import { readFileSync } from 'node:fs';
import { posix } from 'node:path';
import type { Project } from '../store.js';
import { typeOf, under } from './files.js';
import { HttpError, type Handler } from './index.js';

const PAGE_BYTES = 12 << 20;
/** The page's own scripts and styles run; nothing in it may call out, send a form or move its base. */
const POLICY = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' data: blob:; `
  + `style-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; media-src data: blob:; base-uri 'none'; form-action 'none'">`;

export function preview(project: (id: string) => Project): [string, RegExp, Handler][] {
  const unpacked = (): never => { throw new HttpError(501, 'Results are not packed into a zip here; download each file by itself.'); };
  return [
    ['GET', /^\/api\/projects\/([^/]+)\/artifact\/preview$/, ({ params: [id], url }) => {
      const { workdir } = project(id!);
      const found = under(workdir, url.searchParams.get('path') ?? '');
      if (!found || typeOf(found.path)[0] !== 'html') throw new HttpError(404, 'no such HTML result');
      if (found.stat.size > PAGE_BYTES) throw new HttpError(413, 'This page is too large to preview; download it instead.');
      // The page is shown from what is sent here, not served as a page of this origin.
      return { ...page(workdir, found.path, readFileSync(found.file, 'utf8')), served_page: false };
    }],
    ['GET', /^\/api\/projects\/[^/]+\/artifact\/bundle$/, unpacked],
    ['POST', /^\/api\/projects\/[^/]+\/artifacts\/bundle$/, unpacked],
  ];
}

/** The page with each local file it names (a src or poster, a link's href, a CSS url()) inlined as
 * a data: URL, a stylesheet's own url()s from where it is. A file outside the workdir, hidden,
 * missing or past the size limit is left out, and named in a warning. */
function page(workdir: string, path: string, html: string) {
  const warnings: string[] = [];
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
  return {
    html: inlinedPage.replace(/^(\s*<!doctype[^>]*>)?/i, `$1${POLICY}`), warnings,
    file_count: 1 + [...inlined.values()].filter(url => url !== 'data:,').length,
  };
}
