# TASK-001：补齐 macOS 单屏开发与启动路径

## 1. 背景

Course Studio 的产品启动入口最初仅面向 Windows，用户需要通过 PowerShell 或 `.cmd` 文件启动。macOS 开发者虽然可以分别构建 Web、配置 Helper 并手动传入参数，但缺少固定、安全、可重复的一键启动入口。

本任务为已经完成首次依赖安装的 macOS 用户提供一条命令或一次 Finder 双击即可启动的单屏路径。单屏路径覆盖资料导入、课程生成、编辑和普通授课，不涉及原生双屏 Host。

当前分支已经包含该任务的一版实现和提交；本文件保留原始任务边界及验收要求，供审查和回归使用。

## 2. 当前行为

- Windows 使用 `platform/start-course-studio.ps1` 和 `platform/启动课程平台.cmd`。
- 当前分支已增加 `platform/start-course-studio.sh` 和 `platform/启动课程平台.command`。
- Shell 启动器从脚本自身位置定位仓库，优先使用仓库根目录 `.venv/bin/python`。
- macOS 数据写入 `~/Library/Application Support/CourseStudio`。
- 缺少 Vite manifest 时，启动器会检查 npm 和本地 Web 依赖并执行 production build。
- Helper 固定监听 `127.0.0.1:8765`，由既有 Helper 逻辑打开浏览器。

## 3. 目标行为

已完成首次安装的 macOS 用户应能够运行：

```bash
./platform/start-course-studio.sh
```

或在 Finder 中双击 `platform/启动课程平台.command`，随后自动完成路径解析、数据目录准备、必要的 Web 构建和 Helper 启动。用户按 `Ctrl+C` 或终止前台进程时，不应遗留由启动器额外创建的后台服务。

依赖缺失、Python 版本错误或 Web 构建失败时，启动器必须失败关闭，并输出清晰、可执行的修复提示。

## 4. 涉及文件

- `platform/start-course-studio.sh`
- `platform/启动课程平台.command`
- `platform/qa/test_start_course_studio.py`
- `platform/docs/README.md`
- 如验收门禁需要：`platform/qa/run.py`、`platform/qa/test_run.py`

## 5. 实现约束

- 以脚本文件自身位置定位仓库，不依赖调用者当前目录。
- 优先且固定使用仓库根目录 `.venv/bin/python`，并校验 Python 3.12。
- 不自动安装 Homebrew、Python、Node 或 npm，不使用 `sudo`，不修改 shell 配置。
- 所有路径必须正确引用，支持空格和中文。
- Helper 仅监听固定 loopback 地址和端口，不接受任意 host、port、command 或 secret 参数。
- `.command` 只调用固定的 `.sh` 文件，不拼接或执行调用者提供的命令。
- 不削弱 Helper 的现有安全边界。
- 保持 Windows 启动行为兼容。
- 不提交 `.venv`、`node_modules`、`dist`、本地数据库、缓存或运行日志。
- 脚本需在 Git 中保留 executable bit。

## 6. 验收标准

- 可从任意当前工作目录运行启动器。
- Finder 双击入口可以调用同目录下固定 Shell 启动器。
- macOS 用户数据位于 `~/Library/Application Support/CourseStudio`。
- 缺少 `.venv`、Python 版本不符、npm 缺失或 Web 依赖缺失时均给出明确错误并非零退出。
- `platform/web/dist/.vite/manifest.json` 不存在时执行 Web build；存在时不重复构建。
- Helper 使用固定的 `http://127.0.0.1:8765` 和端口 `8765`。
- 空格及中文路径不会导致参数拆分。
- 现有 Windows launcher 测试继续通过。
- 文档明确区分首次安装与日常启动。
- 不声明 macOS 或 Windows 实体双屏已经通过认证。

## 7. 测试方法

运行聚焦测试：

```bash
.venv/bin/python -m pytest platform/qa/test_start_course_studio.py -q
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
```

根据改动范围运行平台聚焦门禁：

```bash
.venv/bin/python platform/qa/run.py focused
```

在临时数据环境中执行一次真实启动 smoke test，确认页面可通过 loopback 访问，再正常终止进程。必须分别记录自动化测试与真实 smoke test 的结果，不得将前者描述为后者。

## 8. 明确不属于本次任务的内容

- macOS 原生双屏 Host。
- Windows Projection Host、投影协议或实体双屏认证改造。
- 自动安装或升级 Python、Node、npm、Homebrew 或项目依赖。
- 修改课程生成、检索、资料解析或播放业务逻辑。
- 对 Windows 启动器进行无关重写。
- 发布、打包、公证或签名 macOS 原生应用。

