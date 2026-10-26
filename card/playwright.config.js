import { defineConfig, devices } from '@playwright/test';

// Browser tests drive the built card in the demo (card/demo/), with its simulated robot and made-up houses.
// WebGL runs on SwiftShader (software), so machines without a GPU run them too.
export default defineConfig({
  testDir: 'test/browser',
  timeout: 90_000,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  expect: { timeout: 20_000 },
  use: {
    baseURL: 'http://127.0.0.1:8794',
    trace: 'retain-on-failure',
  },
  projects: [{
    name: 'chromium',
    use: {
      ...devices['Desktop Chrome'],
      viewport: { width: 1100, height: 800 },
      launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] },
    },
  }],
  webServer: {
    command: 'python3 -m http.server 8794 --bind 127.0.0.1 --directory ..',
    url: 'http://127.0.0.1:8794/card/demo/',
    reuseExistingServer: !process.env.CI,
    stderr: 'ignore',   // the server logs every request there
  },
});
