import { expect, test, type Page, type Request } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const lifecycle = JSON.parse(readFileSync(resolve(webRoot, ".e2e-runtime", "lifecycle.json"), "utf8"));

type CapturedCall = { type: string; request: Record<string, unknown>; result: Record<string, unknown> };

async function responseJson(response: { json(): Promise<unknown> }): Promise<Record<string, unknown>> {
  return await response.json() as Record<string, unknown>;
}

function isJobResponse(response: { url(): string; request(): Request }, type: string) {
  if (response.url() !== `${lifecycle.helperOrigin}/v1/jobs`) return false;
  try {
    return (response.request().postDataJSON() as Record<string, unknown>).type === type;
  } catch {
    return false;
  }
}

async function waitForReadyImports(page: Page, expected: number) {
  await expect(page.locator(".governed-import--ready")).toHaveCount(expected, { timeout: 60_000 });
}

test("real loopback UI publishes and reopens one evidence-backed course", async ({ page, context }) => {
  test.setTimeout(420_000);
  const calls: CapturedCall[] = [];
  let publishRequest: Request | undefined;
  page.on("response", async (response) => {
    try {
      if (response.url() === `${lifecycle.helperOrigin}/v1/jobs` && response.ok()) {
        const request = response.request();
        const body = request.postDataJSON() as Record<string, unknown>;
        const payload = await response.json() as { result?: Record<string, unknown> };
        if (body.type === "course_publish") publishRequest = request;
        calls.push({ type: String(body.type), request: body, result: payload.result ?? {} });
      }
    } catch {
      // Only strictly parse successful JSON job responses.
    }
  });

  await page.goto(lifecycle.launchUrl);
  await expect(page).toHaveURL(lifecycle.webOrigin + "/");
  const input = page.getByRole("region", { name: "导入课程资料" }).getByLabel("导入资料", { exact: true });
  await input.setInputFiles(lifecycle.fixtures.markdown);
  await waitForReadyImports(page, 1);
  await input.setInputFiles(lifecycle.fixtures.dataset);
  await waitForReadyImports(page, 2);
  await input.setInputFiles(lifecycle.fixtures.pptx);
  await waitForReadyImports(page, 3);

  await page.getByRole("button", { name: "审核与发布知识卡" }).click();
  const reviewList = page.locator('section[aria-label="待审核列表"] li');
  await expect(reviewList.first()).toBeVisible({ timeout: 60_000 });
  let indexed = false;
  for (let index = 0; index < 30; index += 1) {
    const before = await reviewList.count();
    if (before === 0) break;
    const detailResponsePromise = page.waitForResponse((response) => isJobResponse(response, "knowledge_review_detail"));
    await reviewList.first().getByRole("button", { name: "查看" }).click();
    const detailResponse = await detailResponsePromise;
    expect(detailResponse.ok()).toBeTruthy();
    const detailResult = (await responseJson(detailResponse)).result as Record<string, unknown>;
    const task = detailResult.task as Record<string, unknown>;
    const detail = page.locator('section[aria-label="审核详情"]');
    const accept = detail.getByRole("button", { name: "接受" });
    await expect(accept).toBeVisible({ timeout: 30_000 });
    const resolveResponsePromise = page.waitForResponse((response) => isJobResponse(response, "knowledge_review_resolve"));
    const listResponsePromise = page.waitForResponse((response) => isJobResponse(response, "knowledge_review_list"));
    const refreshedDetailPromise = page.waitForResponse((response) => {
      if (!isJobResponse(response, "knowledge_review_detail")) return false;
      const request = response.request().postDataJSON() as Record<string, unknown>;
      return request.taskId === task.taskId;
    });
    await accept.click();
    expect((await resolveResponsePromise).ok()).toBeTruthy();
    const listResponse = await listResponsePromise;
    expect(listResponse.ok()).toBeTruthy();
    const listResult = (await responseJson(listResponse)).result as { items: Array<Record<string, unknown>> };
    const refreshedDetailResponse = await refreshedDetailPromise;
    expect(refreshedDetailResponse.ok()).toBeTruthy();
    const refreshedDetail = (await responseJson(refreshedDetailResponse)).result as Record<string, unknown>;
    await expect(reviewList).toHaveCount(before - 1, { timeout: 30_000 });

    const sameSubjectOpen = listResult.items.some((item) => item.subjectVersionId === task.subjectVersionId);
    if (refreshedDetail.cardVersionId && !sameSubjectOpen) {
      expect(refreshedDetail.cardVersionId).toBe(task.subjectVersionId);
      const publish = detail.getByRole("button", { name: "发布并等待索引" });
      await expect(publish).toBeVisible({ timeout: 30_000 });
      const publishResponsePromise = page.waitForResponse((response) => isJobResponse(response, "knowledge_card_publish"));
      const indexResponsePromise = page.waitForResponse((response) => isJobResponse(response, "knowledge_index"));
      await publish.click();
      const publishResponse = await publishResponsePromise;
      if (!publishResponse.ok()) throw new Error(`KNOWLEDGE_CARD_PUBLISH_FAILED:${await publishResponse.text()}`);
      expect((await indexResponsePromise).ok()).toBeTruthy();
      await expect(page.getByText(/知识卡已发布/).last()).toBeVisible({ timeout: 60_000 });
      indexed = true;
    }
  }
  expect(indexed).toBeTruthy();
  await page.getByRole("button", { name: "关闭知识审核" }).click();

  await page.getByRole("button", { name: "下一步：生成课程" }).click();
  await page.getByLabel("课程名称").fill("证据优先的 AI 课程");
  await page.getByLabel("课程受众").fill("个人 AI 学习者");
  await page.getByLabel("课程时长（分钟）").fill("45");
  await page.getByLabel("课程目标").fill("理解真实来源、知识卡与课程发布的证据链\n真实来源图形");
  await page.getByRole("button", { name: "组合课程大纲" }).click();
  await expect(page.getByRole("heading", { name: "可调整课程大纲" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "确认大纲并创建课程" }).click();
  await expect(page.getByRole("button", { name: "打开真实图形与发布" })).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: "打开真实图形与发布" }).click();
  const governed = page.getByRole("dialog", { name: "真实图形与发布" });
  await governed.getByRole("button", { name: "绑定" }).first().click();
  await expect(governed.getByText(/图形已绑定/)).toBeVisible({ timeout: 60_000 });
  await governed.getByRole("button", { name: "生成真实图表" }).click();
  await expect(governed.getByText(/图表已从固定数据集/)).toBeVisible({ timeout: 60_000 });
  await governed.getByRole("button", { name: "检索" }).click();
  await governed.getByRole("button", { name: "获取并绑定" }).first().click();
  await expect(governed.getByText(/图形已绑定/)).toBeVisible({ timeout: 60_000 });
  await governed.getByRole("button", { name: "发布课程" }).click();
  await expect(governed.getByText(/课程已发布/)).toBeVisible({ timeout: 60_000 });
  await page.screenshot({ path: resolve(webRoot, "output", "playwright", "published-editor.png"), fullPage: true });

  const published = calls.findLast((call) => call.type === "course_publish")?.result;
  expect(published?.courseVersionId).toBeTruthy();
  expect(published?.slideDeckId).toBeTruthy();
  expect(published?.runtimeManifestId).toBeTruthy();
  expect(publishRequest).toBeTruthy();
  const replay = await page.evaluate(async ({ url, body, session }) => {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Course-Session": session },
      body,
    });
    return { ok: response.ok, payload: await response.json() };
  }, {
    url: publishRequest!.url(),
    body: publishRequest!.postData()!,
    session: publishRequest!.headers()["x-course-session"],
  });
  expect(replay.ok).toBeTruthy();
  expect((replay.payload as { result: Record<string, unknown> }).result).toEqual(published);

  const reopenResponse = page.waitForResponse((response) => response.url().includes("/v1/courses/") && response.url().includes("/projection"));
  await page.reload();
  const reopened = await responseJson(await reopenResponse);
  expect(reopened.courseVersionId).toBe(published!.courseVersionId);
  expect((reopened.slideDeck as Record<string, unknown>).versionId).toBe(published!.slideDeckId);
  expect((reopened.runtimeManifest as Record<string, unknown>).versionId).toBe(published!.runtimeManifestId);
  await page.getByRole("button", { name: "双屏授课" }).click();
  await page.getByRole("button", { name: "检查屏幕并开始" }).click();
  await page.getByRole("button", { name: "进入同屏排练" }).click();
  await expect.poll(() => context.pages().length, { timeout: 30_000 }).toBe(3);
  const stage = context.pages().find((candidate) => candidate.url().includes("view=stage"));
  const presenter = context.pages().find((candidate) => candidate.url().includes("view=presenter"));
  expect(stage).toBeTruthy();
  expect(presenter).toBeTruthy();
  await expect(stage!.getByText("证据优先的 AI 课程").first()).toBeVisible({ timeout: 30_000 });
  await expect(presenter!.getByText("证据优先的 AI 课程").first()).toBeVisible({ timeout: 30_000 });
  for (const projectionPage of [stage!, presenter!]) {
    const images = projectionPage.locator('.slide-visual-gallery img');
    await expect(images).toHaveCount(3, { timeout: 30_000 });
    await expect.poll(
      () => images.evaluateAll((items) => items.every((item) => (item as HTMLImageElement).complete && (item as HTMLImageElement).naturalWidth > 0)),
      { timeout: 30_000 },
    ).toBe(true);
    await expect(projectionPage.locator('.slide-visual-gallery .visual-fallback')).toHaveCount(0);
  }
  await expect(page.getByText("物理双屏认证：未认证")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "排练已就绪" })).toBeVisible({ timeout: 30_000 });
  await stage!.screenshot({ path: resolve(webRoot, "output", "playwright", "stage.png"), fullPage: true });
  await presenter!.screenshot({ path: resolve(webRoot, "output", "playwright", "presenter.png"), fullPage: true });

  const receipt = {
    schemaVersion: 1,
    status: "verified",
    mode: "fixture-backed-loopback",
    browserPolicySha256: createHash("sha256").update(readFileSync(resolve(webRoot, "e2e", "browser-policy.json"))).digest("hex"),
    operations: calls.map((call) => ({
      type: call.type,
      operationId: call.request.operationId ?? null,
      resultIds: Object.fromEntries(Object.entries(call.result).filter(([key, value]) => /(?:Id|Ids|Digest)$/.test(key) && (typeof value === "string" || Array.isArray(value)))),
    })),
    published: {
      courseVersionId: published!.courseVersionId,
      slideDeckId: published!.slideDeckId,
      runtimeManifestId: published!.runtimeManifestId,
      runtimeManifestDigest: published!.runtimeManifestDigest,
      courseProjectionId: published!.courseProjectionId,
    },
    checks: {
      exactOperationReplay: true,
      byteBoundReopen: true,
      stagePresenterSharedProjection: true,
      physicalDualScreenCertified: false,
      liveNetworkAuthorizationCertified: false,
      protectedSourceAccessed: false,
    },
  };
  const evidenceDir = resolve(webRoot, "evidence");
  mkdirSync(evidenceDir, { recursive: true });
  const temporary = resolve(evidenceDir, "course-composition-browser-e2e.tmp");
  const final = resolve(evidenceDir, "course-composition-browser-e2e.json");
  writeFileSync(temporary, JSON.stringify(receipt, null, 2) + "\n", "utf8");
  renameSync(temporary, final);
});
