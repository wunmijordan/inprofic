const { defineConfig, devices } = require('@playwright/test');
const baseURL = `http://127.0.0.1:${process.env.INPROFIC_UI_PORT || '8765'}`;
module.exports = defineConfig({
  testDir: './tests/ui', fullyParallel: false, workers: 1, timeout: 90000,
  expect: { timeout: 10000 }, retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL, trace: 'retain-on-failure', screenshot: 'only-on-failure', ...devices['Desktop Chrome'],
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {} },
  webServer: { command: `${process.env.INPROFIC_UI_PYTHON || 'venv/bin/python'} scripts/ui/server.py`, url: `${baseURL}/accounts/login/`, reuseExistingServer: false, timeout: 180000, stdout: 'ignore', stderr: 'ignore' },
});
