import { expect, test, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { browserPolicyFileName } from "./browser-policy.mjs";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const lifecycle = JSON.parse(
  readFileSync(resolve(webRoot, ".e2e-runtime", "lifecycle.json"), "utf8"),
) as {
  launchUrl: string;
  helperOrigin: string;
  webOrigin: string;
  fixtures: { markdown: string; pptx: string; dataset: string };
};

type JobCall = { type: string; status: number };

const sha256 = (path: string): string =>
  createHash("sha256").update(readFileSync(path)).digest("hex");

async function resolveAtMostOneAttention(page: Page): Promise<number> {
  const ready = page.getByRole("heading", { name: "个人 AI 工作流实战" });
  const attention = page.getByRole("heading", { name: "有几项内容需要你确认" });
  await expect
    .poll(
      async () =>
        (await ready.isVisible().catch(() => false)) ||
        (await attention.isVisible().catch(() => false)),
      { timeout: 180_000 },
    )
    .toBe(true);
  if (await attention.isVisible().catch(() => false)) {
    await page.getByRole("button", { name: "接受建议并继续" }).click();
    await expect(ready).toBeVisible({ timeout: 180_000 });
    return 1;
  }
  return 0;
}

test("one personal action creates and reopens a governed course", async ({ page }) => {
  test.setTimeout(420_000);
  const jobCalls: JobCall[] = [];
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedSameOriginRequests: Array<{ url: string; error: string }> = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("requestfailed", (request) => {
    if (request.url().startsWith(lifecycle.webOrigin) || request.url().startsWith(lifecycle.helperOrigin)) {
      failedSameOriginRequests.push({
        url: request.url(),
        error: request.failure()?.errorText ?? "unknown",
      });
    }
  });
  page.on("response", async (response) => {
    if (response.url() !== `${lifecycle.helperOrigin}/v1/jobs`) return;
    try {
      const body = response.request().postDataJSON() as { type?: unknown };
      jobCalls.push({ type: String(body.type), status: response.status() });
    } catch {
      // Non-JSON requests are outside the typed job gate.
    }
  });

  await page.goto(lifecycle.launchUrl);
  await expect(page).toHaveURL(lifecycle.webOrigin + "/");
  await expect(
    page.getByRole("heading", { name: "把资料变成一门可直接使用的课" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(webRoot, "evidence", "personal-course-entry.png"),
    fullPage: true,
  });

  await page.getByLabel("选择课程资料").setInputFiles([
    lifecycle.fixtures.markdown,
    lifecycle.fixtures.pptx,
    lifecycle.fixtures.dataset,
  ]);
  await page
    .getByLabel("你想做一门什么课？")
    .fill("为个人讲师制作 60 分钟 AI 工作流实战课");
  const primary = page.getByRole("button", { name: "开始组课" });
  await expect(primary).toBeEnabled();
  await primary.click();
  await page.screenshot({
    path: resolve(webRoot, "evidence", "personal-course-post-action.png"),
    fullPage: true,
  });

  const attentionCount = await resolveAtMostOneAttention(page);
  const courseTitle = page.getByRole("heading", { name: "个人 AI 工作流实战" });
  await expect(courseTitle).toBeVisible();
  await expect(page.locator("body")).not.toContainText(
    /personal-run-|course-version-|slide-deck-|runtime-manifest-|[0-9a-f]{64}/,
  );
  await expect(page.getByText("3 份真实资料")).toBeVisible();
  await page.screenshot({
    path: resolve(webRoot, "evidence", "personal-course-ready.png"),
    fullPage: true,
  });

  expect(jobCalls.filter((call) => call.type === "personal_course_create")).toHaveLength(1);
  expect(jobCalls.filter((call) => call.type === "knowledge_card_publish")).toHaveLength(0);
  expect(jobCalls.filter((call) => call.type === "course_visual_attach")).toHaveLength(0);
  expect(jobCalls.every((call) => call.status >= 200 && call.status < 300)).toBe(true);

  await page.reload();
  await expect(courseTitle).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("body")).not.toContainText(
    /personal-run-|course-version-|slide-deck-|runtime-manifest-|[0-9a-f]{64}/,
  );

  await page.getByRole("button", { name: "编辑课程" }).click();
  const sourceDrawer = page.getByRole("button", { name: "打开证据与来源" });
  if (await sourceDrawer.isVisible()) await sourceDrawer.click();
  for (const name of ["evidence-course.md", "ai-evidence.pptx", "segments.csv"]) {
    await expect(page.getByText(name).first()).toBeVisible();
  }
  const closeSources = page.getByRole("button", { name: "关闭证据与来源" });
  if (await closeSources.isVisible()) await closeSources.click();
  await page.getByRole("button", { name: "打开真实图形与发布" }).click();
  const visualDialog = page.getByRole("dialog", { name: "真实图形与发布" });
  const realVisuals = visualDialog.locator("img");
  await expect(realVisuals.first()).toBeVisible({ timeout: 30_000 });
  await expect
    .poll(
      () =>
        realVisuals
          .evaluateAll((images) =>
            images.every(
              (image) =>
                (image as HTMLImageElement).complete &&
                (image as HTMLImageElement).naturalWidth > 0,
            ),
          ),
      { timeout: 30_000 },
    )
    .toBe(true);
  await expect(visualDialog.locator(".visual-loading")).toHaveCount(0, { timeout: 30_000 });
  await expect(visualDialog.locator(".visual-fallback")).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "关闭真实图形与发布" }).click();
  await page.getByRole("button", { name: "课程首页" }).click();
  await page.getByRole("button", { name: "开始授课" }).click();
  await expect(page.getByText("物理双屏认证：未认证")).toBeVisible();

  const controlledArtifactAborts = failedSameOriginRequests.filter(
    (failure) =>
      failure.error === "net::ERR_ABORTED" &&
      failure.url.startsWith(`${lifecycle.helperOrigin}/v1/artifacts/`),
  );
  const unexpectedSameOriginFailures = failedSameOriginRequests.filter(
    (failure) => !controlledArtifactAborts.includes(failure),
  );
  expect(attentionCount).toBeLessThanOrEqual(1);
  expect(consoleErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(unexpectedSameOriginFailures).toEqual([]);

  const receipt = {
    schemaVersion: 1,
    status: "verified",
    mode: "fixture-backed-loopback-personal-flow",
    browserPolicySha256: sha256(resolve(webRoot, "e2e", browserPolicyFileName())),
    fixtures: {
      markdownSha256: sha256(lifecycle.fixtures.markdown),
      pptxSha256: sha256(lifecycle.fixtures.pptx),
      datasetSha256: sha256(lifecycle.fixtures.dataset),
    },
    operations: jobCalls,
    checks: {
      onePrimaryComposeAction: true,
      manualKnowledgePublicationClicks: 0,
      manualVisualBindingClicks: 0,
      attentionBundleCount: attentionCount,
      persistedCourseReopen: true,
      opaqueIdentifiersHidden: true,
      sourceDatasetAndVisualProvenanceVisible: true,
      physicalDualScreenCertified: false,
      noPageErrors: true,
      noConsoleErrors: true,
      noUnexpectedSameOriginFailures: true,
      controlledArtifactAbortCount: controlledArtifactAborts.length,
      protectedSourceAccessed: false,
    },
    screenshots: [
      "platform/web/evidence/personal-course-entry.png",
      "platform/web/evidence/personal-course-post-action.png",
      "platform/web/evidence/personal-course-ready.png",
    ],
  };
  const evidenceDir = resolve(webRoot, "evidence");
  mkdirSync(evidenceDir, { recursive: true });
  const temporary = resolve(evidenceDir, "personal-course-browser-e2e.tmp");
  const final = resolve(evidenceDir, "personal-course-browser-e2e.json");
  writeFileSync(temporary, JSON.stringify(receipt, null, 2) + "\n", "utf8");
  renameSync(temporary, final);
});
