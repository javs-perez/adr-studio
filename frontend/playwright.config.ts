import { defineConfig } from '@playwright/test';

const port = process.env.ADR_TEST_PORT ?? '8766';

export default defineConfig({
  testDir: './tests',
  workers: 1,
  use: { baseURL: `http://127.0.0.1:${port}`, viewport: { width: 1512, height: 982 } },
  webServer: {
    command: 'cd .. && .venv/bin/python -m tests.browser_server',
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
  },
});
