import { defineConfig } from "@playwright/test";
import { verifyBrowserPolicy } from "./e2e/browser-policy.mjs";

// Every supported OS must verify a fixed browser source before Playwright starts.
const browser = verifyBrowserPolicy();

export default defineConfig({
  testDir: "./e2e",
  testMatch: "knowledge-course.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  use: {
    browserName: "chromium",
    headless: true,
    actionTimeout: 15_000,
    launchOptions: { executablePath: browser.executablePath },
    viewport: { width: 1440, height: 960 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  expect: { timeout: 10_000 },
  globalSetup: "./e2e/global-setup.mjs",
  globalTeardown: "./e2e/global-teardown.mjs",
  outputDir: "./output/playwright",
  reporter: [["line"]],
});
