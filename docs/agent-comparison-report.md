# 本仓库 Agent 与主流 Agent 功能对比报告

> 生成于 2026-09-13。范围：全量审查本仓库（LangGraph 自主任务 Agent）核心源码，
> 并与 **OpenAI Codex、Qoder（阿里巴巴）、Hermes Agent（Nous Research）、
> DeepSeek Harness（dsh）** 四款主流 agent 做功能维度对比。
> 本文所有「本仓库」论断均基于源码审查，非 README 宣称。
> **2026-09-15 复核**：P0-A（文件四件套，PR #18）/ P0-B（完成验证，PR #26）/ P0-C（headless，PR #19）
> 已合入，下文能力表、对比表与差距清单已按代码重算；四款对标 agent 的描述未变。

---

## 一、本仓库实际能力盘点（源码依据）

**形态定位**：前后端一体的「自主任务 Agent 平台」——自然语言下发任务 →
LangGraph `StateGraph` 图驱动（planner → risk_scan → subagent_split → executor →
human_confirm → tool → reflect 循环）→ 工具执行 → SSE 实时可视化。
定位是「Agent 运行时 + 通用任务执行」，而非纯编码 agent。

| 维度 | 现状（源码落点） |
|---|---|
| 编排内核 | LangGraph 1.2.x，`main`/`subtask` 双拓扑（`backend/core/agent/graph.py`）；具名路由 + `Literal` 注解声明拓扑 |
| 内置工具 | `web_search` / `read` / `edit` / `glob` / `grep`（P0-A 四件套）/ `file_io`（旧多动作工具，待退役 #17）/ `code_exec`(沙箱) / `http_request` / `memory_search` / `kb_query` / `spawn_subagent` + Git 7 个 + 动态 MCP / OpenAPI / 插件工具（`backend/core/tools/registry.py`）|
| 韧性 | 熔断（closed→open→half_open）+ 指数退避重试（`backend/core/tools/resilience.py`） |
| 上下文 | 截断 / LLM 摘要压缩（默认 8000 tokens，`backend/core/agent/context.py`） |
| 断点续跑 | SqliteSaver checkpoint + `durability="sync"` + `POST /resume`；格式版本打标（`PRAGMA user_version`）、`mode=ro` 只读检测、拒绝语义严格（非 INTERRUPTED / 无 checkpoint / 停在确认闸口一律 409），孤儿任务启动对账 |
| 安全 | EHRB 五类风险扫描 + `human_confirm` 图内确认闸门（决策后重算 `_needs_confirm`）；HTTP 写方法 / MCP 写类 per-call 判定；Git 危险命令黑名单 + 选项注入防护；文件工具（file_io/read/edit/glob/grep）共享沙箱路径白名单 |
| 记忆 / KB | 标准库关键词索引（离线可用），embeddings 默认关闭；**无 AGENTS.md / skills 清单类静态上下文注入**（`prompts.py` 是硬编码常量，任务初始 state 只有一条 user 消息）|
| 入口 | FastAPI Web UI（15 REST + SSE）+ **headless 单发 CLI**（P0-C，`backend/headless.py`：JSON 结果契约 + 四级退出码 + 实例级闸门旁路）|
| 可观测 | SSE 21 类事件 + JSONL Trace 落盘 + 前端回放 |
| 前端 | React 三栏（历史 / 任务流 / Step 详情 + Trace Tab） |
| 工程门禁 | 444 离线测试 + ruff/mypy 全净（零 override）+ 真实模型 live_e2e + CI 五 job |

---

## 二、四款主流 agent 简况

- **OpenAI Codex**：CLI + 桌面 + IDE + Web + SDK 多入口；会话恢复 / 分叉 / 压缩、
  `codex exec` 非交互自动化、`/review` 代码审查；沙箱与审批策略、原生 MCP、
  Skills + 插件市场、AGENTS.md 约定、GitHub/PR 交付、桌面端多线程并行、
  Computer Use、定时任务。
- **Qoder（阿里）**：AI 原生 IDE + CLI + JetBrains 插件 + Cloud Agents；
  Experts Mode 多智能体专家团队并行（规划 / 调研 / 编码 / 审查 / 测试五类专家）、
  Quest 视窗委派长任务、RepoWiki 自动生成代码库文档、增强上下文工程
  （对 100k 文件项目剪裁上下文）、Browser Agent、Spec 驱动开发、
  QoderWake 数字员工（cron）。
- **Hermes Agent（Nous Research）**：自我改进型 agent；持久记忆 + 自动技能创建
  （agentskills.io 标准）、70+ 工具、MCP；多平台消息网关（Telegram / Discord /
  Slack / WhatsApp / …）+ 语音模式；子 agent 并行委托、浏览器自动化 / 视觉 /
  图像生成 / TTS、cron 定时任务、Checkpoints 工作目录快照回滚、批量处理与
  RL 训练数据导出（Atropos / ShareGPT 轨迹）；多执行后端
  （本地 / Docker / SSH / Daytona / Modal / Singularity）。
- **DeepSeek Harness (dsh)**：「一切皆插件」的 Cordis 内核（模型 / 工具 / skills /
  会话 / 沙箱 / 存储 / 循环 / 调度 / UI 全部插件化）；append-only session 日志
  （Resume / Fork / Search / Replay 全在同一事件流上）；四种运行模式
  （Standard / Code mode 模型写 TS 编排工具 / Minimal / Creator）；
  文件系统 6 工具 + `write_todos` 规划 + 子 agent + HITL 审批 + 事件流；
  本地 Web UI（127.0.0.1:3080）+ headless CLI。

---

## 三、逐维度能力对比

| 维度 | 本仓库 | Codex | Qoder | Hermes | DeepSeek Harness |
|---|---|---|---|---|---|
| **编排范式** | Plan-and-Execute（StateGraph 显式图） | ReAct 循环（隐式） | 多专家流水线 | ReAct + 记忆驱动 | ReAct + Plan 工具 + Code mode |
| **文件编辑** | read(行号分页) / edit(str_replace) / glob / grep 四件套（P0-A、PR #18）；旧 file_io 待退役 | 精读精确编辑 | IDE 级编辑 | 文件工具集 | 6 个文件工具（精确替换）|
| **Shell / 代码执行** | `code_exec` subprocess（仅超时，无容器隔离） | 强沙箱 + 审批策略 | 终端工具 | 多后端（Docker / SSH / Modal）加固 | Minimal 内置持久 bash |
| **Skills 系统** | 无运行时（skills 仅作 KB 文档被检索） | 原生 Skills + 插件市场 | Skills + 插件 | 自动创建 / 复用技能 | skills 即插件 |
| **上下文管理** | 截断 / 摘要压缩 | 会话压缩 / 分叉 | 增强上下文工程 + RepoWiki 精剪 | 持久记忆 + 摘要 | tool 结果 eviction 落盘换引用 |
| **记忆 / 约定注入** | 跨会话关键词 KB；**无 AGENTS.md 类约定注入**（prompts 硬编码、初始 state 只一条 user 消息）| AGENTS.md | 记忆 + Memo | MEMORY.md / USER.md + Honcho 用户建模 | storage 插件 |
| **断点 / 恢复** | Checkpoint + resume + 格式守卫（深度工程化） | 会话恢复 / 分叉 | Quest 状态追踪 | Checkpoints 回滚 | session 日志 resume / fork / replay |
| **不可变审计** | JSONL Trace | — | — | — | append-only trajectory 视图 |
| **人工确认闸门** | 风险扫描 + 确认 / 拒绝重算、写副作用防重试 | 审批模式 | 审批 | 命令审批 | 工具审批（HITL） |
| **韧性** | 熔断 + 指数退避 | 重试 | — | — | — |
| **团队 / 子 agent** | 隔离子图线程池并行，防递归 | 子 agent | Experts 专家团并行 | 并行子 agent | task 子 agent |
| **浏览器 / 多模态** | 无浏览器；无视觉 / 图像 / TTS | 浏览器 / Computer Use / 图像 | Browser Agent | 浏览器自动化 + 视觉 + 图像 + TTS + 语音 | — |
| **MCP** | stdio，动态注册 | 原生多传输 | MCP | MCP + 过滤 | 社区插件（非核心） |
| **Git / 平台交付** | 本地 git 7 工具（无 PR / issue） | GitHub / PR 深度集成 | 集成 | GitHub 技能 | — |
| **定时 / 自动化** | 无 cron | 定时任务 | QoderWake / Cloud | cron + 消息网关 | scheduling 插件 |
| **入口** | Web UI + headless 单发 CLI（P0-C、PR #19）；无交互式常驻会话 | CLI + 桌面 + IDE + Web + SDK | IDE + CLI + 插件 + Cloud + 移动 | CLI + 桌面 + IDE + 消息多平台 | Web UI + CLI |
| **训练数据 / RL** | 无 | — | — | 批处理 + Atropos + 轨迹导出 | trajectory 视图 |
| **沙箱隔离强度** | 弱（subprocess 超时；Docker 需外部提供） | 强（ro 沙箱 + 审批） | 云端 / 终端 | 容器加固（ro root / 降权 / PID 限制） | 沙箱插件化 |

---

## 四、关键差异研判

### 本仓库强于主流、但主流往往不强调的部分

1. **断点续跑的执行位置等价性工程**（`backend/services/task_manager.py`）：
   checkpoint 格式打标、`mode=ro` 只读检测（拒绝动作本身不改写被拒文件）、
   拒绝挂载时服务照常起、`durability` 只在挂了 checkpointer 时传——把
   「读得回」和「跑得续」分开论证。这是闭源产品不会暴露给用户的深度，
   属本仓库最独特资产。
2. **工具层韧性一等公民**：熔断 + half-open 试探 + 指数退避 +
   写副作用 `retryable=False`（重试一个已副作用过的调用比重试失败更糟）。
   主流 agent 基本只做简单重试。
3. **确认闸门的循环根因修复**（`backend/core/agent/nodes.py` `human_confirm_node`）：
   `_confirmed_ids/_rejected_ids` 决策后重算 `_needs_confirm`，配合
   `_after_tool` 重入判定，修掉单工具流死循环。
4. **可验证性**：444 个确定性离线测试 + MockLLM + ruff/mypy 全净 +
   真实模型 E2E，口径远强于多数闭源产品。

### 本仓库明显落后 / 缺失的部分

1. ~~**无编程精读工具**~~ **已补（P0-A / PR #18）**：`read`（行号分页）/ `edit`（精确
   `str_replace`）/ `glob` / `grep` 四件套已落地，与 Codex / dsh / Hermes 的六件套同构；
   旧 `file_io` 待退役（Issue #17）。原差距描述保留作记录：那曾是「任务执行器」与
   「能操作文件世界的 agent」之差。
2. **无上下文注入层**（2026-09-15 复核新增；本表最被低估的一项）：`core/agent/prompts.py`
   是两段硬编码常量（无插值、不读外部文件），`task_manager.py` 的任务初始 state 只有一条
   user 消息，`AGENTS.md` 运行时从不被读（全仓引用只有 3 处注释文字），`.agents/skills/`
   只有 3 个 reference 被脚本手工拷进 KB，连沙箱根的绝对路径都不告诉模型。对标列里
   Codex 的「AGENTS.md」、Hermes 的「MEMORY.md / USER.md」、dsh 的会话预设是同一层能力
   ——**四重收敛**，证据强度超过本表任何一项。推导与优先级见
   [`capability-first-principles.md`](capability-first-principles.md) §七。
3. **无 Skills 运行时**：`.agents/skills/` 仅作 KB 文档检索（`SKILL.md` 本身从未被索引），
   无「技能清单注入 + 正文按需加载」机制。**依赖上一项**：没有注入层就没有「清单」这一半。
4. **无浏览器自动化与多模态**：主流几乎标配；本仓库完全没有。
5. **沙箱隔离偏弱**：仅 subprocess + 超时，无容器 / ro / syscall 隔离。
6. **记忆 / 上下文工程偏原始**：关键词检索（embeddings 关闭）、无 RepoWiki
   类代码库文档、无跨会话用户建模、无 tool-result eviction。
7. **自动化面仍薄**：headless 单发已补（P0-C / PR #19），但无 cron、无消息多平台、
   无交互式常驻会话（Issue #20）。
8. **无 GitHub/PR 平台交付**：只有本地 git 命令。
9. **架构可扩展性范式不同**：dsh「一切皆插件」、Qoder 多专家组合；
   本仓库是单一任务图 + 配置开关，无组合编排能力（Code mode 类）。

---

## 五、结论

- **定位本质差异**：本仓库是「通用自主任务 Agent + 运行时工程深度」；
  Codex / Qoder / Hermes / dsh 是「面向编码 / 多表面交付的产品级 harness」。
- **本仓库不可替代的护城河**在状态机正确性（checkpoint/resume 语义、
  确认闸门、熔断），这些恰是主流产品藏在内部不展示的部分。
- **若要更接近主流**，合理增强优先级见
  [`docs/capability-first-principles.md`](capability-first-principles.md)
  （第一性原理推导）与 [`docs/roadmap-pawbench.md`](roadmap-pawbench.md)
  （PawBench 驱动的落地路线）。
- 一句话：**在「我靠什么让你信这套运行时工程扎实」上本仓库领先；
  在「我能帮你把代码活干完、在你所有入口存在」上明显落后于主流。**
  二者并非同一量级产品，更偏互补。

---

## 参考来源

- OpenAI Codex CLI：<https://learn.chatgpt.com/zh-Hans/docs/codex/cli>
- Qoder 官方文档 / 1.0 发布博客：<https://docs.qoder.com/zh/product-series/what-is-qoder> · <https://qoder.com/en/blog/qoder-1.0>
- Hermes Agent 官方文档（Nous Research）：<https://hermes-agent.nousresearch.com/docs/zh-Hans/>
- DeepSeek Harness 官方：<https://deepseek.com/harness/en/> · <https://github.com/deepseek-ai/deepseek-harness>
