# 个人课程平台

## 使用

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

当前自动验收覆盖一键组课、持久化重开、知识与图形来源、生产构建和 Windows Host 合同。实体 Win11 双屏与逐窗口全屏仍需在真实双显示器设备上有人值守认证；认证前必须保持 `physicalDualScreenCertified: false`。

## 当前证据

- `web/evidence/personal-course-browser-e2e.json`
- `web/evidence/personal-course-ready.png`
- `helper/evidence/reference-demo-receipt.json`
- `windows/evidence/projection-integration.json`

亮色界面基准：`docs/course-studio-light-reference.png`。

physical dual-screen: NOT CERTIFIED

final result: passed
