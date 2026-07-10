import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';

const outfile = 'bundle/argus.mjs';

await build({
  entryPoints: ['src/cli.tsx'],
  outfile,
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node18',
  minify: true,
  banner: {
    js: "import { createRequire as __argusCreateRequire } from 'node:module'; const require = __argusCreateRequire(import.meta.url);",
  },
  define: { 'process.env.NODE_ENV': '"production"' },
});

// A dependency embeds a template-literal line ending in a space. Normalize
// generated line endings so `git diff --check` remains a useful release gate;
// this changes no executable semantics.
const bundled = await readFile(outfile, 'utf8');
await writeFile(outfile, bundled.replace(/[ \t]+$/gm, ''), 'utf8');
