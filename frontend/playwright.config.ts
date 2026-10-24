import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8123',
    ...devices['Desktop Chrome'],
    // 优先复用本机 Chrome；CI 可改成安装 Playwright Chromium 后移除此项。
    channel: 'chrome',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'LC_ALL=C ../backend/.venv/bin/python ../scripts/e2e_server.py',
    url: 'http://127.0.0.1:8123/api/health',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
