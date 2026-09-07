import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E Test Configuration
 * 
 * Tests run against local wrangler dev server (port 8788)
 * Start with: npx wrangler dev
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // Sequential for auth state dependency
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1, // Single worker for consistent state
  reporter: [
    ['html', { outputFolder: 'e2e-report' }],
    ['list'],
  ],
  
  use: {
    baseURL: 'http://localhost:8788',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 10000,
    navigationTimeout: 15000,
  },

  projects: [
    {
      name: 'chromium',
      use: { 
        ...devices['Desktop Chrome'],
        viewport: { width: 1280, height: 720 },
      },
    },
  ],

  // Local dev server - uncomment if you want Playwright to start it automatically
  // webServer: {
  //   command: 'npx wrangler dev',
  //   url: 'http://localhost:8788',
  //   reuseExistingServer: !process.env.CI,
  //   timeout: 60000,
  // },
});
