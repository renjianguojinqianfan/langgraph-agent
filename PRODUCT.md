# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

单人自用的开发者：作者本人在本机（Windows + Git Bash 环境）使用，日常在真实项目目录里下发任务，让 agent 读写文件、跑 bash，停下时直接去工作区看文件。不做多用户、不做分发；偶尔作为求职简历项目向面试官演示，并用于学习 LangGraph 与 agent 工程。

## Product Purpose

把「自然语言任务 → 自主执行的本地 agent」做成能每天真实使用的工具：一个任务是一段可中断、可续说的多轮对话，模型在真实工作区里用工具干活，人随时能停下审阅、改计划、继续。成功 = 作者本人愿意每天用它干真活，并且能在面试中把这条运行时讲清楚。

## Positioning

看得见的运行时。相邻产品可以抄界面与工具列表，但这条机制链是核心：一条 loop（一个 `agent` 节点）、计划 / 执行两模式只换 system prompt 与工具面、停下 = 本轮最后一次调用没有工具调用、停下后沿同一 `thread_id` / checkpoint 续说、交付 = 人去看工作区文件（没有「完成」终态）、每步与每次工具调用都进 SSE 与 JSONL trace 可回放。形态上先求「最简、一条命令启动」：本机 `qianfan` 一条命令起后台 + 网页。

## Operating Context

- 启动：`qianfan` 单后台 + 浏览器；第二次在别的目录启动只交接给同一后台并定位到该目录。开发时 `python start.py`（后端 `--reload` + Vite 热重载）。
- 工作区：在哪个目录启动，新任务就在哪个目录读写、跑 bash、加载该目录的 `AGENTS.md` 与 `.agents/skills`；页面 `?dir=` 可指定其他目录。
- 模型：设置页管理多套「地址 + 模型 + Key」（OpenAI 兼容），Key 存仓库外 `~/.qianfan/credentials.json` 或配置里写的环境变量；预设只有 DeepSeek。
- 观测：网页端 SSE 实时可视化 + trace 回放 / 导出；运行数据落在 `data/`（任务记录、trace、checkpoint、日志），代码撤销靠 git。
- 也作为简历项目被讲解：README、ADR 与验证证据是面试素材；需要能 3 分钟讲清项目、画出图拓扑、讲出关键工程故事。

## Capabilities and Constraints

已确认能力（README「能力」）：文件工具（read / write / edit / ls / glob / grep）、`bash`、`web_search`（duckduckgo / serpapi / exa）、MCP、子代理（explore / execute，工具面只能收窄）、两层 AGENTS.md 注入、工具结果逐出与上下文压缩、工作区文件列表 / 预览 / 下载、SSE + trace、计划文件（任务目录 `.qianfan/plans/<task_id>.md`）、模型配置、页面内切换工作区、历史删除。

硬约束（本次确认）：

- 中文界面与中文文档。
- 仅本机使用：只监听 `127.0.0.1`，一台机器共用一个后台；`auth_enabled` 只减少暴露面，不是权限 / 安全边界；`bash` 拿运行账号权限。
- 工具面仿 pi：read / write / edit / ls / glob / grep / bash / web_search / MCP / 子代理；不再扩内置工具，新能力优先 MCP 或以后章节的插件。
- 不做 Docker / 远程访问；当前章不分发。
- 视觉权威是 `docs/frontend-design.md`；未来视觉与交互工作以它为准，`DESIGN.md` 只作它的机器可读镜像，不是第二套权威。
- 术语与状态机是契约：任务状态四值 `PENDING / RUNNING / INTERRUPTED / FAILED`，没有 `COMPLETED`；「计划」有两个意思（计划文件 vs 票面计划），见 `GLOSSARY.md`。

明确未定（记录而非发明）：

- 模型配置是否走向 Provider 化 / 多模型目录 / Anthropic 协议（#243 待定）。
- 插件 + 预设机制（#67，下一章）。
- 交互式终端入口（#20）。
- 是否 / 如何分发（当前章不分发，属以后章节的决策）。

## Brand Commitments

- 中文，直白、具体、不道歉、不模糊的界面文案（设计基线 §7）。
- 「安静的监控台」是已交付并评审过的视觉方向；`docs/frontend-design.md` 是视觉权威（此处只记约束，不展开视觉配方）。
- 产品名 / 命令：`qianfan`；标题「LangGraph 自主任务 Agent」。

## Evidence on Hand

- 仓库文档与决策：`README.md`、`docs/architecture.md`（REST / SSE 契约）、`docs/adr/0001`–`0010`、`docs/frontend-design.md`（设计基线）、`docs/manual-verification.md`（9 个真模型场景的结果表）。
- 真实界面与验收证据：`docs/assets/qianfan-ui.png`；`docs/assets/verification/2026-10-21/`、`2026-10-22/`、`2026-10-27/`（截图 + 脱敏 JSON）。
- 离线测试与 CI：backend pytest（本机 2026-11-05 实跑 839 passed / 1 skipped）、frontend vitest 165 tests、CI 三个 job 全绿。
- 不得编造：没有外部用户 / 客户 / 用户证言，没有第三方 benchmark、定价或商业承诺。

## Product Principles

1. 运行时优先于演示：价值在续说、checkpoint、两模式、危险拦截、工具生态与可观测，不在界面花活。
2. 停下来让人看：模型负责跑，人负责在停下时审阅工作区文件；不发明「完成」与假进度。
3. 最简可启动：一条命令、一个后台、一个网页；不为「看起来完整」保留半套能力。
4. 记录是唯一权威：状态、计划版本来自任务快照（REST）；SSE 只给节奏，不反写状态。
5. 先说清边界：安全、权限、分发与未知项都写进文档，不靠默认值假装安全。

## Accessibility & Inclusion

按 `docs/frontend-design.md` §6 的可访问性基线执行：键盘可达且焦点可见、页签语义化、状态 `aria-live`、对比度正文 ≥4.5:1、目标 ≥24×24、`prefers-reduced-motion` 下停位移与循环、原生 `<dialog>` 焦点管理。无其他已确认的用户特定需求。
