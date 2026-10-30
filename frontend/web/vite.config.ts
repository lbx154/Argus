import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';
import { readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { gzipSync } from 'node:zlib';

// Dev: proxy /api → the local argus webapi (argus --web, default :8799).
// Prod: `vite build` emits static assets the API serves from frontend/web/dist.
const API = process.env.ARGUS_WEB_API ?? 'http://127.0.0.1:8799';
let webOutput: string;

export default defineConfig({
  base: './',
  plugins: [react(), {
    name: 'precompressed-static',
    apply: 'build',
    // Both the local build and packages/core/scripts/web.ts use this config.
    configResolved(config) { webOutput = resolve(config.root, config.build.outDir); },
    closeBundle() {
      // Include copied public files as well as Vite's chunks and HTML.
      for (const entry of readdirSync(webOutput, { recursive: true, withFileTypes: true })) {
        if (!entry.isFile() || !/\.(?:html|js|css|svg|json|webmanifest|wasm)$/.test(entry.name)) continue;
        const file = join(entry.parentPath, entry.name);
        const original = readFileSync(file);
        const compressed = gzipSync(original, { level: 9 });
        if (compressed.length < original.length) writeFileSync(`${file}.gz`, compressed);
        else rmSync(`${file}.gz`, { force: true });
      }
    },
  }],
  server: {
    port: 5173,
    fs: { allow: [fileURLToPath(new URL('..', import.meta.url))] },
    proxy: {
      '/api': { target: API, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    // Hosted pages allow same-origin assets under a strict CSP, not data URLs.
    assetsInlineLimit: 0,
    // Open tabs can still request chunks from a previous release. Vite replaces
    // index.html and emits new fingerprinted assets without deleting those chunks.
    emptyOutDir: false,
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/gsap/')) return 'motion';
          if (id.includes('react-markdown') || id.includes('remark-') || id.includes('rehype-') || id.includes('/katex/') || id.includes('micromark') || id.includes('mdast') || id.includes('hast')) return 'markdown';
          if (id.includes('@fortawesome')) return 'icons';
          if (id.includes('@tanstack/react-query')) return 'query';
          return undefined;
        },
      },
    },
  },
});
