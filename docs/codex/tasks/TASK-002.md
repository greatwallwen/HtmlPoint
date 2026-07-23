# TASK-002：建立 macOS CI 基线

## 1. 背景

项目包含 Python Helper、React Web、浏览器测试以及 Windows 原生投影 Host。此前主要验证说明和工具链面向 Windows，缺少可重复执行的 macOS 持续集成基线，因而无法及时发现 Python、Node、文件路径和平台判断方面的回归。

本任务建立 macOS 单屏代码路径的 CI，不承担原生投影或实体双屏认证。当前分支已经包含 `.github/workflows/macos-ci.yml` 的一版实现和提交；本文件保留任务范围及验收依据。

## 2. 当前行为

- 当前分支存在名为 `macOS CI` 的 GitHub Actions workflow。
- workflow 在 pull request 和 `master` push 时运行。
- runner 使用 `macos-latest`、Python 3.12 和 Node.js 22。
- Python Helper 通过 editable dev install 安装，Web 通过 lockfile 和 `npm ci` 安装。
- CI 执行 Python QA、离线 Helper 测试、Web 单元测试、typecheck 和 production build。
- 明确排除了 reference demo、网络视觉、模型下载、投影 integration 和投影 hardware 测试。

## 3. 目标行为

每个 pull request 以及主分支 push 都应在干净的 macOS runner 上验证：

1. Python QA 聚焦测试；
2. 不需要网络、模型下载、Windows Host 或真实硬件的 Helper 测试；
3. Web 单元测试；
4. TypeScript typecheck；
5. Web production build。

失败必须阻止 CI job 成功，且同一 PR 的旧运行可以在新提交到达后取消。

## 4. 涉及文件

- `.github/workflows/macos-ci.yml`
- 仅在存在明确跨平台失败证据时：`platform/qa/run.py`
- 仅在存在明确测试假设错误时：对应的 QA 或 Helper 测试文件

## 5. 实现约束

- 使用 `macos-latest` 和项目要求的 Python 3.12。
- Node 版本应明确固定为项目支持版本；依赖必须使用 `npm --prefix platform/web ci` 安装。
- GitHub Actions 权限保持最小，只授予 checkout 和测试需要的只读权限。
- 设置合理的 job 超时和 concurrency cancellation。
- 缓存只能优化下载，不能成为正确性前提。
- 不依赖开发机 `.venv`、`.tools`、模型缓存、预生成 `dist` 或用户资料目录。
- 不执行网络视觉、模型下载、Windows Projection Host、投影 integration 或实体硬件测试。
- 测试阶段不得通过 `continue-on-error`、忽略退出码或删除测试制造绿色结果。
- 不为通过 CI 而无关升级依赖或修改产品业务逻辑。
- macOS 单屏 CI 不构成实体双屏认证。

## 6. 验收标准

- Workflow YAML 有效，并在 pull request 与 `master` push 上触发。
- `permissions` 为最小只读权限。
- 同一 PR 的旧运行会被取消。
- job 具有明确超时。
- Python 3.12 环境和 Helper dev dependencies 安装成功。
- Node 环境和 `npm ci` 安装成功。
- Python QA 测试通过。
- 排除明确外部依赖后的 Helper 测试通过。
- Web 单元测试、typecheck 和 production build 通过。
- Windows-only、联网、模型下载和真实硬件测试没有被误执行。
- 没有宽泛的允许失败设置。
- 不生成或提交运行产物与本地缓存。

## 7. 测试方法

在 macOS 本地使用干净依赖环境执行与 workflow 等价的命令：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e "platform/helper[dev]"
npm --prefix platform/web ci

.venv/bin/python -m pytest platform/qa/test_run.py platform/qa/test_start_course_studio.py -q
.venv/bin/python -m pytest platform/helper/tests \
  -m "not reference_demo and not network_visual and not model_download and not projection_integration and not projection_hardware" \
  -q
npm --prefix platform/web test -- --run --exclude "**/*.projection-integration.test.ts"
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
```

随后由 GitHub Actions 实际运行一次 workflow。交付记录需区分“本地等价命令通过”和“GitHub macOS runner 通过”；只有后者可以证明 CI 基线真实可用。

## 8. 明确不属于本次任务的内容

- macOS 启动器或 Finder 双击入口的实现。
- 跨平台 Playwright 浏览器信任策略。
- 下载或认证真实浏览器的发布证据链。
- Windows 原生 Host 的构建、修复或认证。
- 实体双屏测试。
- 网络视觉、embedding 模型下载及在线集成测试。
- 依赖版本全面升级或 CI 矩阵扩展到 Linux、Windows、多版本 Python/Node。

