import { defineConfig } from '@playwright/test';

const port = Number(process.env.ARGUS_DESKTOP_UI_PORT || '1428');
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid isolated UI test port');
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  timeout: 30_000,
  reporter: 'list',
  outputDir: './build/ui-test-results',
  use: {
    baseURL,
    browserName: process.platform === 'darwin' ? 'webkit' : 'chromium',
    channel: process.platform === 'win32' ? 'msedge' : undefined,
    viewport: { width: 1280, height: 820 },
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `npm run ui:dev -- --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: false,
  },
});
