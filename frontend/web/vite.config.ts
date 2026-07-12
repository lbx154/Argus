import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// Dev: proxy /api → the local argus webapi (argus-skill --web, default :8799).
// Prod: `vite build` emits static assets the API serves from frontend/web/dist.
const API = process.env.ARGUS_WEB_API ?? 'http://127.0.0.1:8799';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    fs: { allow: [fileURLToPath(new URL('..', import.meta.url))] },
    proxy: {
      '/api': { target: API, changeOrigin: true, ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('react-markdown') || id.includes('remark-') || id.includes('micromark') || id.includes('mdast') || id.includes('hast')) return 'markdown';
          if (id.includes('@fortawesome')) return 'icons';
          if (id.includes('@tanstack/react-query')) return 'query';
          return undefined;
        },
      },
    },
  },
});
