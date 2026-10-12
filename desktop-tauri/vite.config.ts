import { defineConfig } from 'vite';
import { resolve } from 'node:path';

const host = process.env.TAURI_DEV_HOST;
const brand = process.env.ARGUS_DESKTOP_BRAND
  ? JSON.parse(process.env.ARGUS_DESKTOP_BRAND) : null;
if (brand !== null && (
  typeof brand.name !== 'string' || !brand.name.trim()
  || typeof brand.mark !== 'string' || typeof brand.markDark !== 'string'
  || !brand.mark.startsWith('data:image/') || !brand.markDark.startsWith('data:image/')
)) {
  throw new Error('ARGUS_DESKTOP_BRAND requires name and embedded mark/markDark images');
}

export default defineConfig({
  root: resolve(__dirname, 'src'),
  clearScreen: false,
  base: './',
  define: { __ARGUS_DESKTOP_BRAND__: JSON.stringify(brand) },
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
