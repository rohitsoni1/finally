import { defineConfig, devices } from '@playwright/test';

/**
 * FinAlly E2E config.
 *
 * Local:  BASE_URL defaults to http://localhost:8000 (see run-local.sh which boots the backend
 *         with LLM_MOCK=true and a throwaway SQLite DB).
 * Docker: docker-compose.test.yml sets BASE_URL=http://finally:8000.
 *
 * The suite shares one backend + one database, so it runs serially (workers: 1) and spec files
 * are numbered to fix their order. 01-fresh-start assumes a freshly seeded DB.
 */
const BASE_URL = process.env.BASE_URL || 'http://localhost:8000';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    viewport: { width: 1600, height: 1000 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1600, height: 1000 } } }],
});
