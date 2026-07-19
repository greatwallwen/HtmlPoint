import { defineConfig } from "@playwright/test";
import { verifySystemChrome } from "./e2e/browser-policy.mjs";

const browser = verifySystemChrome();

export default defineConfig({
  testDir: "./e2e",
  testMatch: "knowledge-course.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  globalSetup: "./e2e/global-setup.mjs",
  globalTeardown: "./e2e/global-teardown.mjs",
  outputDir: "./output/playwright",
  reporter: [["line"]],
  use: {
    browserName: "chromium",
    headless: true,
    launchOptions: { executablePath: browser.executablePath },
    viewport: { width: 1440, height: 960 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
});
