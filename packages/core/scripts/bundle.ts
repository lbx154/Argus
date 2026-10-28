// The published package: the CLI with the packages it uses in one file, dist/argus.mjs, beside
// the prompts it reads (prompts/), Argus's own skills (skills/) and the web cockpit
// (web/, scripts/web.ts).

import { build } from 'esbuild';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const core = fileURLToPath(new URL('..', import.meta.url));
await build({
  entryPoints: [join(core, 'dist/cli.js')], outfile: join(core, 'dist/argus.mjs'),
  bundle: true, platform: 'node', format: 'esm', target: 'node22',
});

