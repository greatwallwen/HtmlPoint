# TASK-002 独立复审

## 复审结论

结论：**仍为有条件通过；上一轮唯一问题未解决。**

workflow 配置和本地等价命令继续通过，未发现本轮 TASK-001/003 修复对 CI 测试选择或构建造成回归。但 GitHub 上仍没有与实现提交关联的真实 workflow run，因此无法证明 `macos-latest`、Node 22、全新安装和远端 YAML 组合实际可用。

GitHub 只读复核结果：提交 `1cc976f01a9d5196116fc5993174de4bf9d7f230` 的 pull-request workflow runs 返回空数组。实施报告将此项标为“未解决”与实际一致。

> 三个 task 共用分支是预期安排，不作为问题。TASK-002 实现范围仍按提交 `1cc976f` 核对。

## 上一轮问题复核

### 1. 尚无真实 GitHub macOS runner 成功记录

- **状态**：未修复
- **严重程度**：中
- **文件和代码位置**：`.github/workflows/macos-ci.yml:1-65`；`docs/codex/reports/TASK-002-implementation.md` 的“GitHub macOS runner 真实通过”和“审查意见处理结果”
- **问题原因**：本地测试不能证明 GitHub 接受 workflow、干净 editable install 和 `npm ci` 成功、Node 22 兼容、marker 在 macOS runner 上选择正确或 job 能在 30 分钟内完成。代码没有变化并不可能自行产生远端验收证据。
- **触发方式**：push 包含 workflow 的新提交或创建 PR 后触发 `macOS CI`；当前远端实现提交没有关联的 PR workflow run。
- **推荐修改方式**：在获得授权后 push/创建 PR，保存成功 run URL、commit SHA、runner 信息和各 step 结果；只有真实 run 全绿后才能将该项改为完成。若失败，依据远端日志修复，不应继续用本地 Node 24 结果替代 Node 22 runner。

## 新回归检查

- workflow 仍在 PR 和 `master` push 触发，权限仍只有 `contents: read`。
- concurrency cancellation、30 分钟超时、Python 3.12、Node 22 和 `npm ci` 未被削弱。
- 无 `continue-on-error` 或退出码屏蔽。
- Helper 离线选择仍为 `864 passed, 17 skipped, 62 deselected`。
- Web 套件因 TASK-003 新增测试从 346 增至 362，兼容 Node 24 下全部通过。
- TASK-001 QA 新增测试使 QA/launcher 共享回归增至 192，全部通过。
- 未发现公共字段、JSON/Unity 序列化或产品业务实现变化。
- 一次将 Helper 与完整 Web 套件并行执行的压力复核中，`test_real_spawn_dispatches_only_the_module_level_allowlisted_jobs` 因时间上限返回 504；该测试单独重跑通过，随后按 workflow 的串行方式重跑完整 Helper 选择也全部通过。workflow 本身顺序执行这两个 step，因此当前证据不构成 TASK-002 回归，但说明不应把高负载并行结果当作 CI 等价命令。

## 验收标准复核

| 验收项 | 结果 |
| --- | --- |
| workflow 触发、权限、concurrency、超时 | 配置通过 |
| Python 3.12、Helper dev install | 配置通过；本轮未重建 venv |
| Node 22、`npm ci` | 配置通过；真实 runner 未验证 |
| Python QA | 本地通过 |
| 离线 Helper 测试 | 本地通过 |
| Web tests/typecheck/build | 兼容 Node 24 本地通过 |
| 外部/模型/投影/硬件排除 | 通过 |
| 无宽泛允许失败 | 通过 |
| GitHub macOS runner 真实通过 | **未通过验收：没有 run** |

## 本次执行的验证

```text
.venv/bin/python -m pytest platform/qa/test_start_course_studio.py platform/qa/test_run.py -q
192 passed

.venv/bin/python -m pytest platform/helper/tests \
  -m "not reference_demo and not network_visual and not model_download and not projection_integration and not projection_hardware" -q
864 passed, 17 skipped, 62 deselected in 71.63s

Node.js 24.14.0：
npm --prefix platform/web test -- --run --exclude "**/*.projection-integration.test.ts"
37 files passed；362 tests passed

npm --prefix platform/web run typecheck
npm --prefix platform/web run build
均通过

GitHub commit workflow runs (`1cc976f...`)
total_count: 0
workflow_runs: []
```

## 无法验证的内容

- `macos-latest` 真实 run、Node 22 和全新 runner 环境。
- 全新 `.venv` editable install 与全新 `node_modules` 的 `npm ci`。
- 被明确排除的联网、模型下载、投影 integration 和硬件测试。
