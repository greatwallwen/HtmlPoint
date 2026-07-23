# TASK-003：实现跨平台 Playwright 浏览器策略

## 1. 背景

Web E2E 会生成截图和 receipt，并参与项目的验收证据，因此浏览器不能仅凭 PATH 或调用者提供的任意 executable 启动。原实现固定查找 Windows Chrome，并通过 PowerShell、Authenticode、版本、发布者和 SHA-256 进行验证，无法在 macOS 上执行。

本任务在不削弱 Windows 证据链的前提下，为 macOS 增加明确、失败关闭、可单元测试的系统 Chrome 策略。

当前工作区已经存在该任务的未提交实现。任务审查时必须先审阅这些既有改动，不得覆盖或假设其已通过最终验收。

## 2. 当前行为

- `browser-policy.mjs` 已被改为按 `process.platform` 分发 Windows 与 macOS 策略。
- Windows 逻辑已拆入 `browser-policy-windows.mjs`，继续检查 Authenticode、版本、发布者、路径与 SHA-256。
- macOS 逻辑已加入 `browser-policy-darwin.mjs`，固定检查系统级和用户级 Google Chrome `.app` 候选路径。
- macOS 策略当前检查 bundle/executable/plist 的类型、符号链接和 realpath，并调用 `codesign`、`plutil`、`lipo`，最后验证版本、bundle identifier、Team Identifier、架构和 executable SHA-256。
- `browser-policy.darwin.json` 当前固定了一份 macOS Chrome 策略。
- 相关 Playwright、QA、teardown、E2E spec 和单元测试文件存在未提交改动。
- 不支持的平台会返回稳定的 `E2E_BROWSER_POLICY_MISMATCH`。

## 3. 目标行为

Playwright E2E 在 Windows 与 macOS 上都必须先获得经策略验证的浏览器描述对象，再使用其中的 `executablePath` 启动浏览器。

Windows 应保持原有 Authenticode 安全语义；macOS 应仅信任固定候选位置、合法 Google 签名、预期 bundle metadata、允许架构及匹配 hash 的 Chrome。任何策略、路径、签名、版本、hash、命令执行或平台不匹配均应失败关闭，不得静默退回 PATH 或任意 Chromium。

成功结果应提供稳定、统一的字段，包括 platform、browser family、executable path、product version、provenance、policy version、hash 和 verification status。对外失败信息不得泄漏用户目录或内部异常细节。

## 4. 涉及文件

- `platform/web/e2e/browser-policy.mjs`
- `platform/web/e2e/browser-policy-windows.mjs`
- `platform/web/e2e/browser-policy-darwin.mjs`
- `platform/web/e2e/browser-policy.json`
- `platform/web/e2e/browser-policy.darwin.json`
- `platform/web/playwright.config.ts`
- `platform/web/e2e/global-setup.mjs`
- `platform/web/e2e/global-teardown.mjs`
- `platform/web/e2e/knowledge-course.spec.ts`
- `platform/web/src/browser-policy.test.mjs`
- 如证据门禁确有需要：`platform/qa/run.py`、`platform/qa/test_run.py`

## 5. 实现约束

- 使用明确的平台分发；Windows 与 macOS 之外的平台失败关闭。
- Windows 不得删除或降低 Authenticode、publisher、version、固定候选路径和 SHA-256 检查。
- macOS 不仅靠应用名称判断真实性，必须验证 `.app` bundle、内部 executable、metadata 与签名身份。
- macOS 外部命令使用固定绝对路径和参数数组调用，并设置超时；不得使用 `eval` 或 shell 字符串拼接。
- 不允许通过 PATH 或环境变量指定任意浏览器 executable。
- 不允许检测到本机 Chrome 后自动改写仓库策略。
- bundle、executable 和 plist 的符号链接或非预期 realpath 必须被拒绝。
- 所有失败向调用方收敛为稳定、去敏的错误，不暴露用户名或 Home 路径。
- 单元测试通过依赖注入或 mock 覆盖平台及命令执行，不依赖当前机器真实安装 Chrome。
- 策略 JSON 必须经过严格结构校验。
- 必须说明 Chrome 自动更新后固定版本/hash 不匹配将导致 E2E 失败，并给出人工、可审计的策略更新流程；不能静默放宽。
- 普通 macOS 单屏 E2E 不得被描述为实体双屏认证。

## 6. 验收标准

- Windows 和 macOS 均选择对应策略文件及验证器。
- 不支持的平台稳定失败。
- Windows 原有安全检查保持有效，并有回归测试。
- macOS 验证固定候选位置、bundle identifier、Team Identifier、版本、架构、签名和 executable hash。
- 浏览器缺失、策略无效、路径替换、符号链接、签名错误、版本错误、hash 错误、外部命令失败或超时均失败关闭。
- 成功时 Windows 和 macOS 返回一致的描述对象结构。
- Playwright 只使用策略返回的 executable path。
- 错误 message 与 stack 不包含用户绝对路径。
- 路径含空格或中文时参数不会被错误拆分。
- teardown 与 receipt 仍遵守原有清理和证据语义。
- Web 单元测试、typecheck、build 通过。
- 真实 macOS Chrome E2E 若执行，必须使用当前策略完全验证；若未执行，则明确标记为未验证。

## 7. 测试方法

运行浏览器策略聚焦测试：

```bash
npm --prefix platform/web test -- --run platform/web/src/browser-policy.test.mjs
```

如果 Vitest 的路径解析要求相对于 `platform/web`，则使用实际可工作的等价路径，并在交付记录中写明最终命令。

运行 Web 全部非集成验证：

```bash
npm --prefix platform/web test -- --run --exclude "**/*.projection-integration.test.ts"
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
```

运行平台聚焦门禁：

```bash
.venv/bin/python platform/qa/run.py focused
```

在本机 Chrome 的版本、签名和 hash 与策略一致时，执行：

```bash
npm --prefix platform/web run test:e2e
```

测试报告必须分别列出单元测试、构建、QA 门禁和真实浏览器 E2E；不得用 mock 测试代替真实浏览器证据。

## 8. 明确不属于本次任务的内容

- macOS 启动器和 Finder 双击入口。
- macOS CI workflow 的创建或扩展。
- 自动下载、安装或升级 Google Chrome。
- 自动接受 Chrome 更新后的新版本或 hash。
- 使用 PATH、任意环境变量或调用者路径作为浏览器逃生口。
- macOS 原生双屏 Host、Windows Projection Host 或投影协议改造。
- 实体双屏认证。
- 修改课程生成、资料解析、检索或播放业务功能。
- 无关依赖升级、全仓格式化、自动 commit、push 或创建 PR。

