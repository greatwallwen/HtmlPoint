# TASK-003 实施报告：跨平台 Playwright 浏览器策略

## 修改的文件

- `platform/web/e2e/browser-policy.mjs`
- `platform/web/e2e/browser-policy-schema.mjs`
- `platform/web/e2e/browser-policy-windows.mjs`
- `platform/web/e2e/browser-policy-darwin.mjs`
- `platform/web/e2e/browser-policy.darwin.json`
- `platform/web/playwright.config.ts`
- `platform/web/e2e/global-setup.mjs`
- `platform/web/e2e/global-teardown.mjs`
- `platform/web/e2e/README.md`
- `platform/web/e2e/knowledge-course.spec.ts`
- `platform/web/src/browser-policy.test.mjs`
- `platform/web/src/global-teardown.test.mjs`
- `platform/qa/run.py`
- `platform/qa/test_run.py`

这些文件是当前工作区中的未提交实现；现有 Windows `platform/web/e2e/browser-policy.json` 未被改写。

## 每个文件修改了什么

### `platform/web/e2e/browser-policy.mjs`

- 按 `process.platform` 明确分发 `win32` 和 `darwin` 策略；其他平台失败关闭。
- 选择固定的平台策略文件并加载 JSON，不接受 executable 环境变量或 PATH fallback。
- 将 Windows/macOS 验证结果收敛为统一描述对象。
- 将所有对外失败收敛为 `E2E_BROWSER_POLICY_MISMATCH`，并清理 error stack，避免泄漏用户名、Home 或仓库绝对路径。

### `platform/web/e2e/browser-policy-schema.mjs`

- 为 Windows 与 Darwin 定义共享的严格 policy validator。
- 要求 plain object、精确字段集合、非空格式化版本、SHA-256、平台字段和唯一非空架构数组。
- Windows/Darwin 字段集合互斥，拒绝未知、废弃和混合平台字段。

### `platform/web/e2e/browser-policy-windows.mjs`

- 从原入口拆出 Windows 验证逻辑。
- 保留固定 Chrome 候选、PowerShell 参数数组、10 秒超时、Authenticode、publisher、product/file version 和 SHA-256 验证。
- 额外要求 PowerShell 返回的规范化路径与本次固定候选完全一致。
- 成功后返回统一的 `verified` 浏览器描述；不会回退 PATH 或任意 Chromium。

### `platform/web/e2e/browser-policy-darwin.mjs`

- 只检查系统级和用户级固定 Google Chrome `.app` 路径。
- 验证 bundle、内部 executable 和 Info.plist 的类型、符号链接及 realpath。
- 通过绝对系统工具路径和参数数组调用 `codesign`、`plutil`、`lipo`，每次调用设置 10 秒超时。
- 验证完整 bundle 签名、Team Identifier、bundle identifier、product/bundle version、允许架构和 executable SHA-256。
- 路径、命令、metadata、签名或 hash 任一失败均尝试下一个固定候选，最终稳定失败关闭。

### `platform/web/e2e/browser-policy.darwin.json`

- 增加 schema version 1 的 macOS Chrome allowlist。
- 固定 Google bundle identifier、Team Identifier、版本、内部 executable 相对路径、SHA-256 及 `arm64`/`x86_64` 架构边界。

### `platform/web/playwright.config.ts`

- 在加载 Playwright 配置时先执行平台策略验证。
- `launchOptions.executablePath` 只取自已验证描述对象。
- 注释说明该验证对所有支持的操作系统都是启动前置条件。

### `platform/web/e2e/global-setup.mjs`

- 在 readiness 检查之前写入 Web/Helper PID，使 setup 中途失败时 teardown 仍持有清理目标。

### `platform/web/e2e/global-teardown.mjs`

- Windows 继续使用 `taskkill.exe /T /F` 清理 detached 进程树。
- macOS/POSIX 使用负 PID 对 detached 进程组发送 `SIGTERM`，有界探测存活状态，超时后升级 `SIGKILL` 并再次确认退出。
- 只忽略进程已退出的 `ESRCH`；权限错误和 SIGKILL 后仍存活会明确失败。
- 注释分别说明 Windows 与 POSIX 修改原因和适用系统。

### `platform/web/e2e/README.md`

- 增加 macOS 和 Windows Chrome policy 的人工审计 runbook。
- 固定列出签名、metadata、架构、hash 命令，以及去敏 audit receipt 路径、字段、二人复核和回归命令。

### `platform/web/e2e/knowledge-course.spec.ts`

- receipt schema 保持 version 1。
- `browserPolicySha256` 改为计算本次运行平台实际选择的策略文件，不再在 macOS evidence 中错误绑定 Windows 策略。
- `physicalDualScreenCertified` 仍保持 `false`。

### `platform/web/src/browser-policy.test.mjs`

- 使用 dependency injection/mock 覆盖 Windows/macOS 分发与成功描述对象。
- 覆盖浏览器缺失、路径替换、符号链接、版本、签名身份、publisher、hash、命令失败、命令超时、无效 JSON/策略及不支持平台。
- 覆盖附加/缺失字段、空版本、重复架构、混合平台字段和 platform 错配。
- 验证含空格和中文路径作为单个参数传递。
- 验证对外 message 和 stack 都不包含用户绝对路径。
- 单元测试不依赖开发机真实安装 Chrome。

### `platform/web/src/global-teardown.test.mjs`

- 覆盖 SIGTERM 后正常退出、进程已退出、忽略 SIGTERM 后升级 SIGKILL、权限错误、多个进程组及 SIGKILL 后仍存活。

### `platform/qa/run.py`

- 将浏览器证据门禁从单一 Windows 策略扩展为 Windows/macOS 两个明确策略候选。
- receipt schema 未迁移；门禁根据 receipt 中的 SHA-256 找到实际策略并严格校验对应平台字段。
- 使用与 runtime 等价的精确字段集合、版本、Team Identifier 和唯一架构验证，拒绝 hybrid policy。
- 保留既有 Windows 策略常量兼容测试 fixture。

### `platform/qa/test_run.py`

- 生成 Windows 和 macOS 两种合法策略 fixture。
- 新增 macOS 策略摘要被 course composition gate 接受的测试。
- 新增附加字段、空版本、混合字段、错误平台/Team Identifier 和重复架构的负向 QA 测试。
- 保留 Windows receipt 和实体双屏不得认证的回归断言。

## 验收标准完成情况

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| Windows/macOS 选择对应策略和验证器 | 完成 | dispatcher 及 mock 分发测试 |
| 不支持平台稳定失败 | 完成 | Linux 分支测试 |
| Windows Authenticode/publisher/version/path/hash 不削弱 | 完成 | 原检查保留并增加路径一致性；五类回归测试 |
| macOS 固定位置、bundle、Team ID、版本、架构、签名、hash | 完成 | Darwin 验证器及成功/失败测试 |
| 缺失、无效/混合策略、替换、符号链接、命令错误/超时失败关闭 | 完成 | 27 项策略单测覆盖 |
| 两个平台返回一致描述结构 | 完成 | 成功对象断言 |
| Playwright 只使用验证后的 executable | 完成 | config 直接使用策略返回值 |
| message/stack 去敏 | 完成 | 单测及真实入口输出仅含稳定错误码 |
| 空格/中文路径保持参数边界 | 完成 | mock 命令 argv 测试 |
| teardown 和 receipt 保持语义 | 完成 | 有界退出/升级状态机 6 项测试；receipt v1 和严格 QA digest 验证保留 |
| Web tests、typecheck、build | 完成 | 362 项非投影集成测试、typecheck、build 通过 |
| 真实 macOS Chrome E2E | 未验证 | 本机 Chrome bundle 严格签名验证失败，策略正确拒绝 |
| 不声明实体双屏认证 | 完成 | receipt 继续固定为 `false`；未运行硬件认证 |

## 审查意见处理结果

1. **POSIX teardown 不等待退出或升级清理：已解决。** 实现 SIGTERM、最多 1 秒探测、SIGKILL、再次最多 1 秒确认的有界状态机；新增 6 项测试。setup 在 readiness 前持久化 PID，减少部分启动失败无清理目标的窗口。
2. **策略和 QA 不是严格结构校验：已解决。** 新增共享 JS 精确 schema，Python QA 使用等价精确 key set；新增附加/缺失字段、空版本、重复架构、hybrid 和 platform 错配测试。为保持既有 evidence digest，Windows schema 没有静默新增 `platform` 字段，而是通过精确且互斥的 Windows key set 明确区分。
3. **Chrome 更新流程不可复现：已解决。** 新增 `platform/web/e2e/README.md`，包含两平台固定命令、停止条件、去敏 evidence 位置/字段、二人复核和完整验证步骤。

## 执行的测试

浏览器策略聚焦测试：

```text
npm --prefix platform/web test -- --run \
  src/browser-policy.test.mjs src/global-teardown.test.mjs
2 files passed；33 tests passed
```

QA receipt 聚焦测试：

```text
.venv/bin/python -m pytest platform/qa/test_run.py -q -k "course_composition"
3 passed、175 deselected
```

Web 非投影集成测试及构建：

```text
npm --prefix platform/web test -- --run --exclude "**/*.projection-integration.test.ts"
37 files passed；362 tests passed

npm --prefix platform/web run typecheck
通过

npm --prefix platform/web run build
通过；4686 modules transformed
```

平台 QA：

```text
.venv/bin/python platform/qa/run.py focused
通过；其中 Python QA 178 passed，course composition/evidence gates 通过
```

真实 E2E 入口：

```text
npm --prefix platform/web run test:e2e
系统 Node.js 18.15.0：失败于 ERR_UNKNOWN_FILE_EXTENSION，尚未加载策略

使用工作区 bundled Node.js 24.14.0 重试同一 Playwright 入口
失败关闭：[Error: E2E_BROWSER_POLICY_MISMATCH]
```

本机 Chrome 审计结果：版本 `150.0.7871.125`，但 `codesign --verify --strict` 返回 `invalid signature`。因此没有启动浏览器、没有写入新的 E2E receipt，也没有将 mock 测试表述为真实浏览器通过。

## 未验证内容

- 没有在 bundle 签名、版本、Team Identifier 和 hash 全部匹配策略的 macOS Chrome 上完成真实 Playwright E2E。
- 没有在真实 Windows 主机上重新运行系统 Chrome；Windows 安全语义由 mock 回归测试和未修改的 Windows策略 JSON 证明。
- 没有验证实体双屏、Windows Projection Host 或 macOS 原生双屏 Host。
- 当前 macOS policy 的固定值尚需在具有完整有效签名的对应 Chrome 安装上完成发布级复核。

## 已知风险

- Chrome 自动更新会改变版本或 executable SHA-256，导致 E2E 有意失败；必须人工审计新版本、签名身份、架构和 hash 后提交策略更新，不能自动接受。
- 当前开发机的 Chrome bundle 签名无效，因此无法提供真实 macOS 浏览器 evidence。
- 系统 Node.js 18.15.0 无法加载当前 Playwright TypeScript 配置；真实 E2E 需要项目支持的兼容 Node 版本。
- macOS 用户级候选包含 Home 下固定的 `Applications/Google Chrome.app`；虽然错误已去敏且 realpath/symlink 被验证，策略维护者仍需审计任何新候选位置。
- receipt v1 通过策略摘要区分平台，没有把浏览器描述对象或绝对 executable path写入 evidence；这避免路径泄漏，但调查时需结合对应策略文件。
