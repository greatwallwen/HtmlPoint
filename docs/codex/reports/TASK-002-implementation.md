# TASK-002 实施报告：macOS CI 基线

## 修改的文件

- `.github/workflows/macos-ci.yml`
- `platform/helper/pyproject.toml`
- `platform/helper/tests/test_embedding_live.py`
- `platform/helper/tests/test_embeddings.py`
- `platform/helper/tests/test_model_cache.py`

实现提交：`1cc976f ci: add macOS single-screen baseline`。

## 每个文件修改了什么

### `.github/workflows/macos-ci.yml`

- 新增名为 `macOS CI` 的 GitHub Actions workflow。
- 在 pull request 和 `master` push 上运行 `macos-latest`。
- 将权限限制为 `contents: read`，配置同一 PR/ref 的 concurrency cancellation。
- 设置 30 分钟 job 超时。
- 固定 Python 3.12 和 Node.js 22，分别使用 editable dev install 与 `npm ci` 安装依赖。
- 执行 Python QA、排除外部/硬件依赖的 Helper 测试、Web 单元测试、typecheck 和 production build。
- 没有 `continue-on-error` 或忽略退出码设置。

### `platform/helper/pyproject.toml`

- 将 setuptools package discovery 限定为 `course_helper*`，关闭 namespace discovery，避免干净安装时把 evidence 等目录误识别为包。
- 注册 `network_visual` 和 `model_download` pytest marker，并保留投影相关 marker。
- 注释说明这些 marker 对应的系统或外部依赖边界。

### `platform/helper/tests/test_embedding_live.py`

- 将依赖 Windows embedding 模型最终化运行时的测试显式标记为 `model_download`。
- CI 通过 marker 表达排除原因，没有删除测试或吞掉失败。

### `platform/helper/tests/test_embeddings.py`

- 将依赖模型运行时/Windows 路径假设的临时目录交换测试标记为 `model_download`。

### `platform/helper/tests/test_model_cache.py`

- 将模型下载、wheel 安装、Windows ACL/job object、生成目录最终化等测试标记为 `model_download`。
- 普通本地 Helper 测试仍保留，只有明确外部或 Windows 运行时边界被排除。

## 验收标准完成情况

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| YAML 在 PR 与 `master` push 触发 | 完成 | workflow `on` 配置 |
| 最小只读权限 | 完成 | `permissions: contents: read` |
| 取消同一 PR/ref 旧运行 | 完成 | concurrency group 与 `cancel-in-progress: true` |
| 明确 job 超时 | 完成 | `timeout-minutes: 30` |
| Python 3.12 和 Helper dev install | 配置完成 | setup-python 3.12；editable `[dev]` install step |
| Node 22 和 `npm ci` | 配置完成 | setup-node 22；lockfile cache 和 `npm --prefix platform/web ci` |
| Python QA | 本地通过 | 当前分支 QA/launcher 192 项通过 |
| 排除外部依赖后的 Helper 测试 | 本地通过 | 864 passed、17 skipped、62 deselected |
| Web 单元测试、typecheck、build | 本地通过 | 362 项非投影集成测试及两个构建命令通过 |
| 不误执行联网、模型下载、Windows/硬件测试 | 完成 | pytest marker expression 和 Web exclude 明确列出边界 |
| 不允许失败 | 完成 | workflow 无 `continue-on-error`，命令未屏蔽退出码 |
| GitHub macOS runner 真实通过 | 未验证 | GitHub API 对远端分支返回 0 个 workflow runs |

## 审查意见处理结果

1. **尚无真实 GitHub macOS runner 成功记录：未解决，需要外部动作。** 本次先确认本地没有 `gh`，随后只读查询公开 GitHub Actions API；远端分支 `macOS/macos-single-screen-launcher` 返回 `total_count: 0`。该问题不能靠代码修改解决，必须 push 新提交或创建 PR 才能触发 workflow。用户未授权本次自动 push/PR，因此没有扩大权限，也没有伪造成功记录。

## 执行的测试

本次报告编写时重新执行：

```text
.venv/bin/python -m pytest platform/qa/test_run.py platform/qa/test_start_course_studio.py -q
192 passed in 8.61s

.venv/bin/python -m pytest platform/helper/tests \
  -m "not reference_demo and not network_visual and not model_download and not projection_integration and not projection_hardware" -q
864 passed, 17 skipped, 62 deselected in 74.82s

npm --prefix platform/web test -- --run --exclude "**/*.projection-integration.test.ts"
37 files passed；362 tests passed

npm --prefix platform/web run typecheck
通过

npm --prefix platform/web run build
通过；4686 modules transformed
```

Web 测试使用工作区提供的 Node.js 24.14.0 执行，因为当前 shell 的系统 Node.js 18.15.0 缺少该测试套件需要的 Web Crypto 行为。workflow 本身固定 Node.js 22，不依赖开发机 Node 18。

尝试查询 GitHub Actions 状态：

```text
gh run list --workflow "macOS CI" ...
未执行：当前环境没有 gh 命令

GET https://api.github.com/repos/greatwallwen/HtmlPoint/actions/runs
  ?branch=macOS%2Fmacos-single-screen-launcher
total_count: 0
```

## 未验证内容

- 没有取得 GitHub Actions 上 `macos-latest` 的真实成功 run，因此只能证明 workflow 配置和本地等价命令，不能证明远端 CI 已实际绿色。
- 本次未重新创建全新的 `.venv`，也未重新运行 `pip install -e "platform/helper[dev]"` 或 `npm ci`；干净安装证据来自实现提交记录，当前复核使用既有依赖环境。
- 未运行被明确排除的网络视觉、模型下载、投影 integration 或投影 hardware 测试。
- 未验证 Windows Projection Host 或实体双屏。

## 已知风险

- `macos-latest` 是 GitHub 管理的移动 runner 标签；系统镜像升级可能暴露新的平台差异。
- workflow 使用 action major tags（如 `actions/checkout@v4`），并非完整 commit SHA 固定。
- 缓存和包注册表暂时不可用会导致安装步骤失败；workflow 不会用旧缓存掩盖安装失败。
- marker 的正确性依赖后续新增外部测试继续被准确标注；未标记的新测试可能被 macOS baseline 意外执行。
- 本地 Web 测试所用 Node 24 与 workflow Node 22 不完全相同，最终兼容性仍需 GitHub runner 证明。
