# TASK-001 实施报告：macOS 单屏开发与启动路径

## 修改的文件

- `platform/start-course-studio.sh`
- `platform/启动课程平台.command`
- `platform/qa/test_start_course_studio.py`
- `platform/qa/smoke_start_course_studio.py`
- `platform/qa/evidence/macos-launcher-smoke.json`
- `platform/docs/README.md`

基础实现提交：`eebcd96 feat(platform): add macOS single-screen launcher`，只包含
`platform/start-course-studio.sh`、`platform/启动课程平台.command`、
`platform/qa/test_start_course_studio.py` 和 `platform/docs/README.md` 的初始实现。

复审修复尚位于当前工作区未提交 diff：`platform/qa/test_start_course_studio.py`
的 build-failure 测试，以及新增的 `platform/qa/smoke_start_course_studio.py`、
`platform/qa/evidence/macos-launcher-smoke.json` 和本实施报告。取得最终 commit
SHA 后应以该 SHA 替换这段工作区说明；当前报告不再把后续修复归属于
`eebcd96`。

## 每个文件修改了什么

### `platform/start-course-studio.sh`

- 从脚本自身目录解析 `platform` 和仓库根目录，不依赖调用者当前工作目录。
- 拒绝所有位置参数，避免把参数解释为命令、secret、host 或 port。
- 固定选择仓库根目录 `.venv/bin/python`，并验证 Python 主次版本为 3.12。
- macOS 数据目录固定为 `~/Library/Application Support/CourseStudio`；同时保留 Linux 的 XDG 数据目录行为。
- 创建应用数据目录、`sources` 目录，并将数据库固定为 `knowledge.db`。
- 检查 `platform/web/dist/.vite/manifest.json`；缺失时检查 npm 和本地 Vite 依赖后执行 production build。
- 使用参数边界明确的 `exec` 启动现有 `course_helper`，固定 `127.0.0.1:8765`、Web 根目录和数据路径。
- 为 `.venv`、Python 版本、npm、Web 依赖和 build 失败提供可执行的错误提示。

### `platform/启动课程平台.command`

- 增加 Finder 双击入口，并保留 Git executable bit。
- 从 `.command` 自身位置解析目录，只 `exec` 同目录固定的 `start-course-studio.sh`。
- 拒绝所有调用者参数，不拼接或执行任意命令。

### `platform/qa/test_start_course_studio.py`

- 保留并继续验证 Windows PowerShell 和 `.cmd` 启动路径。
- 新增 macOS/Linux launcher 的临时环境执行测试。
- 覆盖任意工作目录、含空格路径、macOS Application Support、Linux XDG、固定 loopback/port、manifest build/skip、参数拒绝及依赖失败提示。
- 验证 `.command` 与 `.sh` 的固定调用关系。
- 新增 npm build 返回非零的回归测试，断言 launcher 非零退出、输出修复提示且绝不启动 Helper。

### `platform/qa/smoke_start_course_studio.py`

- 新增可重复的真实 launcher smoke 工具，使用临时含空格/中文 HOME 和真实 `.venv`/Helper。
- 使用 Python `webbrowser` 已支持的标准 `BROWSER=/usr/bin/true` opener 抑制 GUI，不修改产品启动参数。
- 轮询固定 `http://127.0.0.1:8765/`，成功后向真实进程组发送 SIGINT。
- 验证 Helper exit code、HTTP 状态、端口释放和进程组释放，并可写出无用户路径的 JSON evidence。

### `platform/qa/evidence/macos-launcher-smoke.json`

- 记录本次真实 macOS smoke 的 HTTP 200、SIGINT、exit 0、端口释放和进程组释放结果。
- 明确保留 `physicalDualScreenCertified: false`。

### `platform/docs/README.md`

- 增加 macOS 首次安装、依赖安装、日常命令启动和 Finder 双击说明。
- 说明固定 Python 3.12 `.venv`、按需 Web build、数据目录和 `127.0.0.1:8765`。
- 明确该路径只覆盖单屏组课/授课，不包含 Windows Projection Host 或实体双屏认证。

## 验收标准完成情况

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| 任意当前工作目录启动 | 完成 | launcher 以 `$0` 定位仓库；临时目录执行测试通过 |
| Finder 双击调用固定 Shell launcher | 完成 | `.command` 只执行同目录 `.sh`；测试通过 |
| macOS 数据目录位于 Application Support | 完成 | 脚本实现及带空格路径测试通过 |
| 缺少 `.venv`、Python 版本错误、npm 或 Web 依赖时失败关闭 | 完成 | 四类失败路径均有聚焦测试 |
| 缺少 Vite manifest 时 build，存在时跳过 | 完成 | 两条分支均有执行测试 |
| Web build 失败时失败关闭 | 完成 | npm 非零回归测试验证错误提示且 Helper 未启动 |
| 固定 `127.0.0.1:8765` | 完成 | 脚本参数和捕获到的 Helper argv 均被断言 |
| 空格和中文路径参数不拆分 | 完成 | 引号使用正确；空格路径执行测试和中文入口测试通过 |
| Windows launcher 不回归 | 完成 | 同一测试文件中的 Windows 合同测试通过 |
| 首次安装与日常启动文档分离 | 完成 | README 已分别说明 |
| 不声明实体双屏认证 | 完成 | README 明确限定为单屏路径 |
| executable bit | 完成 | 当前文件模式均为 `-rwxr-xr-x` |
| 真实页面可达和正常终止 | 完成 | 真实 Helper 返回 HTTP 200；SIGINT 后 exit 0，端口和进程组释放 |

## 审查意见处理结果

1. **缺少真实启动与正常终止 smoke test：已解决。** 新增受控 smoke 工具和版本化 evidence；真实运行验证了 HTTP 页面、SIGINT、exit code、端口释放和进程组释放。默认浏览器通过标准 `BROWSER` opener 抑制，未改变 launcher/Helper 安全参数。
2. **Web build 失败缺少自动化回归：已解决。** npm stub 支持受控非零退出；新增测试断言 `Web build failed`、可执行下一步提示及 Helper 未启动。
3. **旧实现提交无法追溯后续修复：已解决。** 报告现明确区分基础提交 `eebcd96` 的 4 个文件与当前工作区未提交的复审修复，不再暗示 checkout 旧 SHA 可以取得 smoke 工具、evidence 或第 14 个 launcher 测试。

## 执行的测试

本次报告编写时重新执行：

```text
.venv/bin/python -m pytest platform/qa/test_start_course_studio.py -q
14 passed in 6.88s

.venv/bin/python platform/qa/smoke_start_course_studio.py \
  --evidence platform/qa/evidence/macos-launcher-smoke.json
HTTP 200；SIGINT；exit 0；portReleased=true；processGroupReleased=true
```

共享回归验证：

```text
.venv/bin/python -m pytest platform/qa/test_run.py platform/qa/test_start_course_studio.py -q
192 passed in 8.61s

npm --prefix platform/web run typecheck
通过

npm --prefix platform/web run build
通过；生成 dist/.vite/manifest.json
```

## 未验证内容

- 本次报告编写过程中未实际从 Finder 双击 `.command`；固定调用关系由自动化测试验证。
- smoke 使用标准无 GUI opener，没有验证真实默认浏览器窗口的视觉体验或标签页行为。
- 未在发生端口占用、只读 Home 目录或 macOS Gatekeeper 隔离属性的机器上验证用户体验。
- 未验证任何实体双屏或原生 macOS Host。

## 已知风险

- `127.0.0.1:8765` 是安全边界要求的固定端口；端口被占用时启动会失败，launcher 不会自动换端口。
- Python 被严格固定为 3.12；现有 `.venv` 使用其他版本时必须由用户显式重建。
- Finder 是否提示终端或 Gatekeeper 警告受本机安全设置和文件来源属性影响。
- launcher 依赖用户已完成首次依赖安装，不会自动安装或升级 Python、Node、npm 或项目依赖。
