// The published package: the CLI with the packages it uses in one file, dist/argus.mjs, beside
// the prompts it reads (prompts/) and the built web cockpit (web/).

import { build } from 'esbuild';
import { cpSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const core = fileURLToPath(new URL('..', import.meta.url));
await build({
  entryPoints: [join(core, 'dist/cli.js')], outfile: join(core, 'dist/argus.mjs'),
  bundle: true, platform: 'node', format: 'esm', target: 'node22',
});

// frontend/web/dist keeps every earlier release's chunks for open tabs: only the files
// index.html reaches, directly or through a file it loads, are this release's.
const dist = fileURLToPath(new URL('../../../frontend/web/dist', import.meta.url));
const web = join(core, 'web');
const files = new Map(readdirSync(dist, { recursive: true, encoding: 'utf8' })
  .filter(path => statSync(join(dist, path)).isFile()).map(path => [basename(path), path]));
const reached = new Set(['index.html']);
for (const path of reached) {
  for (const [name] of readFileSync(join(dist, path), 'utf8').matchAll(/[\w.-]+\.\w+/g)) {
    const found = files.get(name);
    if (found) reached.add(found);
  }
}
rmSync(web, { recursive: true, force: true });
for (const path of reached) cpSync(join(dist, path), join(web, path));
