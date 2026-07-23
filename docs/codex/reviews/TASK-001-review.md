# TASK-001 独立复审

## 复审结论

结论：**上一轮问题均已修复，当前实现通过代码审查。**

上一轮两个实现问题均已实际修复：

1. 真实启动 smoke 已在本次复审中重新运行，得到 HTTP 200；发送 SIGINT 后 exit 0，端口和进程组均释放。
2. Web build 非零退出已有自动化测试，验证启动器失败关闭、输出修复提示且不启动 Helper。

本次未发现启动器功能回归、编译错误、公共字段/JSON/Unity 序列化变更或无关业务修改。实施报告现在明确区分基础提交 `eebcd96` 与当前工作区中的复审修复，不再把 smoke、evidence 和第 14 个测试错误归属于旧提交。

> 基准说明：仓库默认分支实际为 `master`。三个 task 共用分支是任务所有者确认的预期安排，不作为问题。

## 上一轮问题复核

### 1. 缺少真实启动与正常终止 smoke test

- **状态**：已修复
- **修复位置**：`platform/qa/smoke_start_course_studio.py:20-117`；`platform/qa/evidence/macos-launcher-smoke.json:1-13`
- **复核证据**：本次在沙箱外重新执行真实 Helper smoke，返回 `httpStatus: 200`、`terminationSignal: SIGINT`、`exitCode: 0`、`portReleased: true`、`processGroupReleased: true`。
- **回归检查**：smoke 从 `/` 作为当前目录启动，使用含空格的临时 HOME；只通过标准 `BROWSER=/usr/bin/true` 抑制 GUI，没有改变 launcher 或 Helper 参数。失败清理路径会对进程组发送 SIGKILL。

### 2. Web build 失败缺少自动化测试

- **状态**：已修复
- **修复位置**：`platform/qa/test_start_course_studio.py:79-90, 114-134, 196-210`
- **复核证据**：npm stub 支持受控非零退出；测试断言返回码为 1、stderr 包含 `Web build failed` 及下一步提示、发生 npm build 调用且没有 Helper Python 调用。启动器聚焦测试包含在本次 `192 passed` 中。
- **回归检查**：正常 build、manifest skip、npm 缺失、Vite 缺失和参数拒绝原有分支仍通过。

### 3. 实施报告引用的实现提交不包含报告新增的修复文件

- **状态**：已修复
- **修复位置**：`docs/codex/reports/TASK-001-implementation.md:11-20`
- **复核证据**：报告现在明确说明 `eebcd96` 只包含 4 个初始文件，并逐项列出仍位于未提交 diff 的 build-failure 测试、smoke 工具、evidence 和报告自身；还要求取得最终 SHA 后替换该工作区说明。
- **回归检查**：checkout `eebcd96` 不再被报告描述为可获得后续修复，当前文件清单与“基础提交/工作区修复”范围一致。

## 新发现的问题

未发现新的实现问题。

## 验收标准复核

| 验收项 | 结果 | 复核依据 |
| --- | --- | --- |
| 任意当前目录运行 | 通过 | 单测及真实 smoke 从 `/` 启动 |
| Finder 固定调用同目录 `.sh` | 代码通过，真实 Finder 未验证 | 固定 `exec`；文件模式 `100755` |
| macOS Application Support 数据目录 | 通过 | 含空格 HOME 测试 |
| 依赖/版本失败关闭 | 通过 | `.venv`、Python、npm、Vite 测试 |
| manifest build/skip | 通过 | 两分支测试通过 |
| build 失败关闭 | 通过 | 新增 npm 非零测试 |
| 固定 loopback/8765 | 通过 | argv 断言及真实 HTTP 200 |
| 空格/中文路径 | 通过 | 单测及 smoke |
| Windows launcher 不回归 | 通过 | QA/launcher 共享测试 |
| 文档区分安装和日常启动 | 通过 | README |
| 正常终止无残留 | 通过 | 真实 SIGINT、端口和进程组探测 |
| 不声明实体双屏认证 | 通过 | evidence 为 false，文档未认证 |

## 本次执行的验证

```text
.venv/bin/python -m pytest platform/qa/test_start_course_studio.py platform/qa/test_run.py -q
192 passed in 13.01s

.venv/bin/python platform/qa/smoke_start_course_studio.py
HTTP 200；SIGINT；exit 0；portReleased=true；processGroupReleased=true

npm --prefix platform/web run typecheck
通过

npm --prefix platform/web run build
通过；4686 modules transformed

.venv/bin/python platform/qa/run.py focused
全部 gate PASS；Python QA 178 passed
```

## 无法验证的内容

- 未从 Finder 真实双击 `.command`，无法验证 Gatekeeper/quarantine 和终端视觉体验。
- smoke 抑制了 GUI 浏览器，没有验证默认浏览器窗口和标签页行为。
- 未验证端口预占、只读 HOME 和真实依赖损坏时的人工体验。
- 未验证任何实体双屏或原生 macOS Host。
