# 个人课程平台

## Windows 使用

1. 双击 `platform/启动课程平台.cmd`。
2. 选择本地 Markdown、演示文稿或 CSV/XLSX 资料。
3. 写一句课程需求，点击“开始组课”。
4. 如出现关注项，确认一次建议后继续。
5. 课程完成后可编辑、刷新后重开，并进入授课视图。

用户数据保存在 `%LOCALAPPDATA%\CourseStudio`，不会写回所选资料目录。

## 首次安装

```powershell
npm.cmd --prefix platform/web ci
python -m pip install -e "platform/helper[dev]"
```

Windows 双屏 Host 的构建工具保存在根目录 `.tools/`。普通单屏组课不依赖实体双屏认证。

## macOS 安装和启动

macOS 单屏开发与授课需要 Python 3.12.x 和 npm。先在仓库根目录确认版本并安装依赖：

```sh
python3 --version  # 必须为 3.12.x
npm --version
python3 -m venv .venv
.venv/bin/python -m pip install -e "platform/helper[dev]"
npm --prefix platform/web ci
```

之后可从任意当前目录直接运行仓库中的脚本：

```sh
./platform/start-course-studio.sh
```

也可以在 Finder 中双击 `platform/启动课程平台.command`。两个入口都不接受外部参数；`.command` 只调用同目录的固定 `.sh` launcher。

launcher 固定使用仓库根目录 `.venv/bin/python`，并要求 Python 3.12；它不会回退到 macOS 系统 Python，也不会自动安装或修改全局环境。缺少 `.venv`、npm 或 Web 依赖时，终端会显示对应的安装命令。

launcher 会在缺少前端 `.vite/manifest.json` 时执行 Web build，然后以固定的 `127.0.0.1:8765` 启动 Python Helper；浏览器只由 Helper 打开。macOS 用户数据默认保存在 `~/Library/Application Support/CourseStudio`。Linux 也可使用同一脚本，数据保存在 `${XDG_DATA_HOME:-$HOME/.local/share}/CourseStudio`。按 Ctrl+C 会结束前台 Helper。

macOS 路径支持资料导入、课程生成、编辑和单屏授课，但不包含 Windows 原生 Projection Host，也不提供 Windows 实体双屏认证。

## 结构

- `web/`：个人组课、编辑、授课与浏览器验收。
- `helper/`：资料解析、知识卡、检索、课程编排、运行任务与证据。
- `contracts/`：投影命令和事件的稳定契约。
- `windows/`：Win11 屏幕检测、窗口分配和投影 Host。
- `qa/`：发布门禁。

## 验证

```powershell
python platform/qa/run.py all
npm.cmd --prefix platform/web run typecheck
npm.cmd --prefix platform/web run build
.tools/dotnet/dotnet.exe restore platform/windows/CourseStudio.ProjectionHost.slnx
.tools/dotnet/dotnet.exe test platform/windows/CourseStudio.ProjectionHost.slnx --no-restore --filter "TestCategory!=projection_integration"
```

macOS/Linux launcher 的聚焦验证：

```sh
.venv/bin/python -m pytest platform/qa/test_start_course_studio.py -q
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
.venv/bin/python platform/qa/run.py focused
```

当前自动验收覆盖一键组课、持久化重开、知识与图形来源、生产构建和 Windows Host 合同。实体 Win11 双屏与逐窗口全屏仍需在真实双显示器设备上有人值守认证；认证前必须保持 `physicalDualScreenCertified: false`。

## 当前证据

- `web/evidence/personal-course-browser-e2e.json`
- `web/evidence/personal-course-ready.png`
- `helper/evidence/reference-demo-receipt.json`
- `windows/evidence/projection-integration.json`

亮色界面基准：`docs/course-studio-light-reference.png`。

physical dual-screen: NOT CERTIFIED

final result: passed
