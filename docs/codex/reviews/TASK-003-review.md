# TASK-003 独立复审

## 复审结论

结论：**上一轮三个问题均已修复，当前实现通过代码审查。**

本次没有因为文件变化而直接判定修复，而是分别验证：

- POSIX teardown 的 SIGTERM 等待、SIGKILL 升级、退出确认和错误传播均有测试；另用真实 detached、忽略 SIGTERM 的进程组验证了清理成功。
- Windows/Darwin policy 使用共享精确字段 schema，Python receipt gate 使用等价约束；附加、缺失、空版本、重复架构、hybrid 和 platform 错配均有负向测试。
- Chrome 更新 runbook 已包含两平台固定命令、停止条件、去敏 evidence、二人复核和完整回归步骤。

33 项策略/teardown 聚焦测试、362 项非投影 Web 测试、typecheck、build、Python receipt 测试和 focused QA 在本次复审中再次全部通过。未发现修复引入新的编译错误、业务回归、公开字段/receipt schema 破坏或 Unity 序列化变化。

真实 macOS Chrome E2E 仍未通过，但这是报告明确保留的未验证项：当前本机 Chrome 签名不满足策略，系统正确失败关闭，未伪造浏览器 evidence。

## 上一轮问题复核

### 1. POSIX teardown 不等待退出或升级清理

- **状态**：已修复
- **修复位置**：`platform/web/e2e/global-teardown.mjs:10-62`；`platform/web/e2e/global-setup.mjs:61-81`；`platform/web/src/global-teardown.test.mjs:12-80`
- **修复证据**：实现先发送 SIGTERM，最多 20 次有界存活探测，未退出则发送 SIGKILL，再次确认；只忽略 ESRCH，EPERM 和 SIGKILL 后仍存活会失败。6 项测试覆盖正常退出、已退出、升级、权限错误、多进程组和 stuck。
- **真实边界验证**：本次额外启动 detached `/bin/sh` 进程组并忽略 SIGTERM，调用真实 `terminatePosixProcessGroup` 后确认负 PID 探测返回 ESRCH，`processGroupReleased: true`。
- **回归检查**：Windows 仍使用 `taskkill.exe /T /F`；setup 在 readiness 前分阶段写入 Web/Helper PID，缩小部分 setup 失败无清理目标的窗口。

### 2. 策略 JSON 与 receipt gate 不是严格结构校验

- **状态**：已修复
- **修复位置**：`platform/web/e2e/browser-policy-schema.mjs:1-65`；`platform/web/e2e/browser-policy-darwin.mjs:7,18-19`；`platform/web/e2e/browser-policy-windows.mjs:4,12-13`；`platform/qa/run.py` 的 policy key set 和 `_browser_policy_is_valid`；相关 JS/Python 负向测试
- **修复证据**：JS 要求 plain object、精确 key set、格式化版本、小写 SHA-256、固定 Darwin platform、10 位 Team ID、非空唯一架构数组；Python gate 按精确 Windows/Darwin key set 分支，不再用宽泛的 OR 接受 hybrid。
- **负向验证**：27 项策略测试覆盖附加/缺失字段、空版本、重复架构、hybrid 和 platform mismatch；Python course-composition 测试覆盖 Windows/Darwin gate 的等价拒绝。
- **兼容性检查**：既有 Windows policy 未添加字段、摘要未被无故迁移；Authenticode、publisher、product/file version、固定候选和 hash 检查仍存在。

### 3. Chrome 策略更新流程不可复现

- **状态**：已修复
- **修复位置**：`platform/web/e2e/README.md:1-90`
- **修复证据**：runbook 为 macOS 列出绝对路径 codesign/plutil/lipo/shasum 命令，为 Windows 列出固定候选、Authenticode、版本和 hash 命令；明确停止条件、audit receipt 路径/字段、去敏要求、二人复核和回归命令。
- **回归检查**：文档明确 mock 不是发布浏览器证据，单屏运行不认证实体双屏。

## 新回归检查

- Playwright 仍只使用 `verifyBrowserPolicy()` 返回的 `executablePath`。
- 不支持平台仍统一失败为去敏的 `E2E_BROWSER_POLICY_MISMATCH`。
- macOS 外部命令仍为固定绝对路径、参数数组和 10 秒超时。
- bundle/executable/plist 的类型、symlink 和 realpath 检查仍在。
- receipt schema 保持 version 1；只把 policy digest 切换为本次平台文件。
- `physicalDualScreenCertified` 仍为 false。
- QA 精确 schema 与 runtime schema 的关键格式约束一致。
- 业务生成、检索、资料解析和播放代码未修改。

## 验收标准复核

| 验收项 | 结果 |
| --- | --- |
| Windows/macOS dispatcher | 通过 |
| 不支持平台失败关闭 | 通过 |
| Windows 安全语义 | 通过 mock 回归；真实 Windows 未复验 |
| Darwin 签名/metadata/版本/架构/hash | 通过 mock 回归；真实合规 Chrome 未取得 |
| 严格策略结构 | 通过 JS 与 Python 负向测试 |
| 描述对象结构一致 | 通过 |
| Playwright executable 绑定 | 通过 |
| message/stack 去敏 | 通过 |
| 空格/中文参数边界 | 通过 |
| teardown 清理语义 | 通过单元测试和真实进程组探针 |
| receipt v1/QA gate | 通过 |
| Web tests/typecheck/build | 通过 |
| 真实 macOS Chrome E2E | 未验证，策略正确拒绝当前无效签名 |
| 不声明实体双屏认证 | 通过 |

## 本次执行的验证

```text
Node.js 24.14.0：
npm --prefix platform/web test -- --run \
  src/browser-policy.test.mjs src/global-teardown.test.mjs
2 files / 33 tests passed

npm --prefix platform/web test -- --run --exclude "**/*.projection-integration.test.ts"
37 files / 362 tests passed

npm --prefix platform/web run typecheck
通过

npm --prefix platform/web run build
通过；4686 modules transformed

.venv/bin/python -m pytest platform/qa/test_start_course_studio.py platform/qa/test_run.py -q
192 passed in 13.01s

.venv/bin/python platform/qa/run.py focused
全部 gate PASS；Python QA 178 passed

真实 POSIX detached process-group probe
SIGTERM 被忽略后升级 SIGKILL；processGroupReleased=true
```

## 实施报告与实际 diff

- 报告列出的 14 个 TASK-003 文件均存在于当前工作区，没有漏列。
- 新增 `browser-policy-schema.mjs`、`global-teardown.test.mjs`、`e2e/README.md` 和 setup PID 修改均已在报告说明。
- Windows `browser-policy.json` 未改写，与报告一致。
- 报告的 33、362、178 项计数均复现。
- 报告如实保留真实 macOS Chrome、真实 Windows 和实体双屏未验证状态。

## 无法验证的内容

- 签名、版本、Team Identifier 和 hash 全部匹配策略的真实 macOS Chrome E2E。
- 真实 Windows Chrome Authenticode 路径和 Windows teardown。
- setup 进程在极端 spawn/文件系统故障下的全部 OS 竞态。
- 实体双屏、Windows Projection Host 或 macOS 原生 Host。
