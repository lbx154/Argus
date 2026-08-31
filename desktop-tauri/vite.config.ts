import { defineConfig } from 'vite';
import { resolve } from 'node:path';

const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  root: resolve(__dirname, 'src'),
  clearScreen: false,
  base: './',
  server: {
    host: host || '127.0.0.1',
    port: 1420,
    strictPort: true,
    hmr: host
      ? {
          protocol: 'ws',
          host,
          port: 1421,
        }
      : undefined,
  },
  build: {
    outDir: resolve(__dirname, 'dist'),
    emptyOutDir: true,
    sourcemap: false,
  },
});
