// The web cockpit the package carries (web/), built from frontend/web's source for this release.
// frontend/web/dist is not used: it keeps every earlier release's files for open tabs.

import { execFileSync } from 'node:child_process';
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

const frontend = fileURLToPath(new URL('../../../frontend/web', import.meta.url));
const web = fileURLToPath(new URL('../web', import.meta.url));
execFileSync(process.execPath, [join(frontend, 'node_modules/vite/bin/vite.js'), 'build',
  '--outDir', web, '--emptyOutDir'], { cwd: frontend, stdio: 'inherit' });
// Beside each file gzip shrinks, its gzipped copy, which the server sends a browser that takes it.
for (const entry of readdirSync(web, { recursive: true, withFileTypes: true })) {
  if (!entry.isFile() || !/\.(?:html|js|css|svg|json|webmanifest|wasm)$/.test(entry.name)) continue;
  const file = join(entry.parentPath, entry.name);
  const original = readFileSync(file);
  const compressed = gzipSync(original, { level: 9 });
  if (compressed.length < original.length) writeFileSync(`${file}.gz`, compressed);
}
