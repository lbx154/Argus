import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  timeout: 30_000,
  reporter: 'list',
  outputDir: './build/ui-test-results',
  use: {
    baseURL: 'http://127.0.0.1:1428',
    browserName: 'chromium',
    channel: process.platform === 'win32' ? 'msedge' : undefined,
    viewport: { width: 1280, height: 820 },
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npm run ui:dev -- --port 1428',
    url: 'http://127.0.0.1:1428',
    reuseExistingServer: false,
  },
});
