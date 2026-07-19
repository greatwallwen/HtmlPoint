# 个人 AI 课程工作台产品设计

**状态：** 已批准进入实现

**批准依据：** 用户确认唯一主任务、激进重置边界、三类受保护目录，并在最终 Image2 稿反馈中明确要求“所有 UI 都是亮色调，然后开始”。此前用户授权后续尽量自主确认。

**视觉事实源：** `docs/product/assets/course-studio-light-reference.png`

**画布：** 1569 × 1002

**SHA-256：** `36A5A9E54C863A326B98CA7082ACF16293EA423D442495E0317D85E91121B3B3`

## 1. 产品结论

平台只服务一条连续任务：个人讲师导入资料，与 AI 协作生成、编辑、验证一门可讲授课程，随后进入双屏授课。

资料库、来源、证据、校验和工具都只能作为当前课程上下文中的能力出现，不得成为独立产品、顶层导航或平行工作台。课程是唯一工作对象，授课是流程终点。

## 2. 成功结果

讲师可以在一次连续会话中完成以下闭环：

1. 导入 Markdown、纯文本或常见文档文件，看到每份资料的明确处理状态。
2. 输入受众、目标和时长，让课程助手生成可编辑的章节与小节结构。
3. 在同一工作区调整标题、描述、时长和顺序，并追溯每个小节使用的资料。
4. 运行课程校验，得到具体问题、通过项以及带稳定摘要的证据收据。
5. 进入排练，打开讲师视图和学员舞台，以会话总线同步当前页、计时和状态。
6. 刷新后恢复最近课程；异常不会静默吞掉用户输入。

## 3. 不在本轮制造的平行产品

- 不建立独立知识库首页、工具市场、证据中心或数据分析首页。
- 不把课程渲染成一组不可编辑的静态幻灯片。
- 不在浏览器中执行任意 Shell 命令。
- 不声称单屏或模拟环境已经认证真实物理双屏。
- 不读取、复制、改写或据此硬编码 `Course_AIProduct/`。
- 不改写 `dataset/` 与 `references/`；本轮界面使用自包含示例数据和用户主动导入的文件。
- 不加入账号、计费、团队权限、云同步或第三方模型凭据管理。

## 4. 唯一信息架构

顶栏用一个线性进度轨道表达全流程：

`导入资料 → 生成课程 → 编辑验证 → 双屏授课`

顶栏之外不存在产品级侧边导航。编辑验证阶段采用三栏加底部协作区：

- 左栏“课程结构”：章节树、小节定位、添加章节。
- 中栏“当前章节”：章节目标、小节卡片、顺序调整、编辑与生成下一讲。
- 右栏“证据与来源”：来源筛选、引用关系、处理状态、校验摘要。
- 底部“课程助手”：可聚焦的文本输入、建议指令、发送、进行中与结果状态。

进入导入或生成阶段时，仍保留同一顶栏与课程上下文，只替换中间主任务区域。进入授课阶段后打开同源窗口，不创建第二套产品架构。

## 5. 视觉系统

### 5.1 强制亮色

所有持久和瞬时界面均使用亮色：主工作区、舞台、讲师视图、缩略图、抽屉、菜单、弹窗、加载、空状态和错误状态。禁止黑色、炭灰、深蓝或暗色舞台面板，禁止渐变。

### 5.2 令牌

- 页面背景：`#F7F8FA`
- 主表面：`#FFFFFF`
- 次表面：`#F4F6F8`
- 边框：`#E3E7ED`
- 主文字：`#172033`
- 次文字：`#667085`
- 品牌蓝：`#1463FF`
- 品牌蓝浅底：`#EAF2FF`
- 成功：`#15803D`
- 警告：`#B45309`
- 错误：`#B42318`
- 圆角：小控件 8px，卡片 12px，面板 14px
- 阴影只用于浮层，内容卡片主要靠边框分层。

正文使用 `Inter, "Noto Sans SC", "Microsoft YaHei", sans-serif`。图标采用 Phosphor Icons 的 regular/duotone 风格；不得用 Emoji、文本字符、手绘 SVG 或 CSS 图形代替图标。

### 5.3 密度与响应

设计基准为 1569 × 1002，最低主要支持宽度为 1180px。三栏桌面比例约为 `23% / 52% / 25%`，底部助手固定在应用内容底部但不得遮挡滚动区域。低于 1180px 时右栏变为可打开抽屉；低于 820px 时显示“请在桌面尺寸编辑课程”的可访问提示，不伪造不可用的三栏编辑体验。

图标按钮最小点击区域 44 × 44px，必须有 `aria-label`、键盘焦点、悬停/焦点提示。弹出层支持 Escape 关闭并把焦点归还触发器。

## 6. 核心状态与数据合同

应用状态必须由可验证的 TypeScript 合同驱动，不能把最终 HTML 当作内容事实源。

```ts
type WorkflowStep = "import" | "generate" | "edit" | "teach";
type ParseStatus = "queued" | "reading" | "ready" | "unsupported" | "failed";
type ValidationLevel = "pass" | "warning" | "error";

interface SourceAsset {
  id: string;
  name: string;
  kind: "markdown" | "text" | "pdf" | "pptx" | "docx" | "web" | "note";
  size: number;
  status: ParseStatus;
  extractedText?: string;
  addedAt: string;
}

interface LessonNode {
  id: string;
  title: string;
  summary: string;
  durationMinutes: number;
  sourceIds: string[];
  status: "draft" | "grounded" | "needs-source";
}

interface ChapterNode {
  id: string;
  title: string;
  objective: string;
  lessons: LessonNode[];
}

interface CourseDocument {
  schemaVersion: 1;
  id: string;
  title: string;
  audience: string;
  goal: string;
  durationMinutes: number;
  chapters: ChapterNode[];
  sources: SourceAsset[];
  updatedAt: string;
}

interface EvidenceReceipt {
  id: string;
  courseId: string;
  kind: "generation" | "validation" | "rehearsal";
  createdAt: string;
  inputDigest: string;
  summary: string;
  checks: Array<{ id: string; level: ValidationLevel; message: string }>;
}
```

持久化只保存结构化课程和证据收据。用户导入文件的原始二进制不进入 `localStorage`；只保存元数据和在浏览器中明确提取出的文本。持久化键带版本号，无法迁移的数据进入可见恢复状态，不静默丢弃。

## 7. AI 协作合同

本轮提供可重复的本地课程助手，实现完整交互而不依赖秘密凭据。它消费 `CourseBrief + SourceAsset[]`，输出结构化 `CourseDocument`，并产生 `generation` 收据。生成规则必须根据受众、目标、时长和来源标题产生结果，不能返回固定截图文案。

助手在编辑阶段支持以下结构化意图：

- “缩短课程到 N 分钟”会按比例调整小节时长并保留至少 5 分钟。
- “为本章补充案例”会在当前章新增带来源关联的小节。
- “检查来源覆盖”会直接运行校验并聚焦问题。

无法识别的请求保留原输入并给出可执行建议，不假装已修改课程。未来模型接入只需替换 `CourseAgent` 接口，不得改写 UI 状态合同。

## 8. 校验与证据

“验证课程”必须检查：

- 课程标题、受众和目标非空。
- 至少一个章节，每章至少一个小节。
- 小节标题与摘要非空，时长为 5–90 分钟。
- 章节内标题不重复。
- 所有引用来源存在且状态为 `ready`。
- 至少 70% 的小节关联来源；不足时为警告，不阻止排练。
- 小节总时长与课程目标时长误差不超过 10%；超过时为警告。

错误阻止进入授课，警告允许继续但必须明确确认。每次校验创建 `validation` 收据，摘要由课程规范化 JSON 的 SHA-256 派生。界面展示校验结果和收据摘要，完整对象保存在本地状态中。

## 9. 双屏授课状态机

授课不是一个“打开两个窗口”的按钮，而是以下状态机：

`idle → checking → permission-required → opening → syncing → ready → presenting`

任一步都可进入 `error`，并提供重试或退回编辑。系统先检测 Screen Details API 能力，再请求权限；无能力时允许打开同屏排练窗口，但清楚标记“排练模式，未认证物理双屏”。

舞台与讲师窗口通过 `BroadcastChannel` 发送当前小节、播放状态、计时和心跳；`localStorage` 保存最后帧用于重连恢复。讲师窗口可前后翻页、开始/暂停、重置计时；舞台窗口只展示当前教学内容和进度。两个窗口始终为全亮色。

## 10. 错误处理

- 文件超出 20MB：导入前拒绝并保留其余队列。
- 不支持的文件：显示 `unsupported`，允许移除，不阻塞可用来源。
- 文本读取失败：显示失败原因与重试按钮。
- 生成输入不完整：在对应字段就地提示并聚焦第一个错误。
- 持久化失败：内存状态继续可用，顶栏显示明确警告。
- 弹窗被浏览器阻止：保留会话，提示允许弹窗并提供重试。
- 会话总线断开：讲师窗口显示重连状态，舞台恢复最后帧。

## 11. 实现边界

新代码仅写入：

```text
platform/
  web/          React + TypeScript + Vite 应用
  qa/           仓库级验收入口与保护检查
docs/
  product/      视觉事实源
  superpowers/  当前设计与实施计划
```

`Course_AIProduct/`、`dataset/`、`references/` 是保护边界。本轮不得对它们执行格式化、生成、复制、批量暂存或删除。每个里程碑前后按顶层路径对比工作树状态，暂存必须显式列出新平台路径，禁止 `git add -A`。

## 12. 验收合同

### 12.1 自动化

- `npm --prefix platform/web test -- --run`：领域合同、生成器、校验器、状态机和主要组件测试全绿。
- `npm --prefix platform/web run typecheck`：无 TypeScript 错误。
- `npm --prefix platform/web run build`：生产构建成功。
- `python platform/qa/run.py all`：保护边界、亮色令牌、结构化合同与构建证据通过。

### 12.2 浏览器主流程

在 1569 × 1002 视口验证：

1. 导入一个 Markdown 文件并看到 `ready`。
2. 输入受众、目标、时长并生成章节。
3. 修改一个小节、调整顺序、关联来源。
4. 运行校验并得到收据摘要。
5. 打开讲师与舞台窗口；在可测试环境中验证消息同步，在单屏环境明确显示排练限制。
6. 刷新主窗口后恢复课程。
7. 测试关键键盘焦点、Escape、错误提示和浏览器控制台无未处理错误。

### 12.3 视觉阻断门

使用 `docs/product/assets/course-studio-light-reference.png` 与相同视口、相同“编辑验证”状态的浏览器截图并排比较。必须检查字体、间距、颜色、图标、文案和响应式行为。所有 P0/P1/P2 差异修复后，`platform/web/design-qa.md` 的最后一行必须为 `final result: passed`。

### 12.4 保护证明

验收前比较实现开始时与结束时的受保护路径状态。新平台提交不得包含 `Course_AIProduct/`、`dataset/`、`references/` 或既存修改的 `AGENTS.md`。孤立 ACL 残留 `.worktrees/platform-reboot/.tmp` 不属于产品实现，不再尝试权限绕过。
