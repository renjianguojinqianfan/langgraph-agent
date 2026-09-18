# 外部 agent 基准评测集调研（决定：adopt 哪一个）

> 生成于 **2026-09-19**。本文是一手来源调研（GitHub 仓库 / 官方站点 / arXiv 原文 / 包元数据），
> 目的是**复核或推翻** [`roadmap-pawbench.md`](roadmap-pawbench.md) v2 的「首个基准 = PawBench」决策，
> 以及 [`pawbench-harness-interface.md`](pawbench-harness-interface.md) 确立的「只借题目 + 每题自带
> `grade()`、自建轻量 runner」形态。
>
> 读者约定：**每条事实后附一手来源 URL**；凡本轮**未亲手在源头核实**的，一律标 **【未证实】**；
> 凡属于推断而非原文陈述的，标 **【推测】**。不写二手博客里的数字——若某数字重要，追到它的所有者。
> **本文只做调研与建议，不改动任何冻结接口。**

---

## 一、一句话结论

**维持 PawBench 作为「对外分数叙事」的尺子与题目格式来源不变；但把 M2 切片的取材口径改为「优先取 MIT/Apache 的原始上游（PinchBench → QwenClawBench → SkillsBench），把 PawBench 当聚合视图而非唯一入口」。**

按用途拆开说，因为答案确实分成两半：

| 用途 | 推荐载体 | 一句话理由 |
|---|---|---|
| **M2 分数叙事**（跨 agent 对比表、失败归因报告、简历主张） | **PawBench 切片（继续）** | 它是本轮唯一同时满足「题面是 Markdown + YAML front-matter」「每题自带可执行 `grade(transcript, workspace_path)`」「deterministic-first + hybrid 0.75 闸门」「自带 7 轴能力标签与 harness-gap 叙事」「仓库 Apache-2.0」的候选。全表扫描后**没有**适配度更高的替代品。 |
| **内部能力迭代 / 快速回归**（每 PR 跑的廉价硬分） | **PinchBench 的 automated 子集（新增）** | PinchBench 是 PawBench 任务格式的**原始出处**，仓库 **MIT**，因此对「同格式题目 + `## Automated Checks` Python 函数」拥有比 PawBench 聚合层**更干净的分发许可**；[`pinchbench/skill`](https://github.com/pinchbench/skill) 里大量 automated 题是零依赖自包含的。 |
| **二期：运行时深度叙事**（护城河：checkpoint/resume/rollback/confirm-gate） | **Claw-Eval 的 PASS³ + 安全一票否决 / HAL 的可靠性维度（参考，不整套接入）** | 这是本轮**最重要的负面发现**：checkpoint/resume/rollback/确认闸门**仍然没有任何基准在测**。最接近的两个设计是 Claw-Eval 的「安全分归零」与 HAL 的「多跑一致性」——值得借来当**自建跑分协议的维度**，但它们本身都要求容器/多服务编排，**不能整套接入**。 |

**不建议**换成 GAIA / Terminal-Bench / SWE-bench / OSWorld / WebArena / AndroidWorld / MLE-bench / TheAgentCompany 作为主基准——它们全部要求每题独立容器、VM/模拟器/浏览器、或 GPU，与本仓库「单 prompt → workdir → 产物落盘、不编排每题容器」的契约冲突（详见 §三、§四）。

---

## 二、候选总表

> 「适配度」= 与本仓库评测契约（`python -m backend.headless -p "<prompt>" --dir <workdir> --auto-approve --output json`，宿主机直接 exec 判分，无每题容器/无 GUI/无 GPU）的匹配程度。
> 「许可」只记一手来源可确证的项；未能确证的写入 §五。

| 名称 | 类型 | 规模 | 评分机制 | 宿主要求 | 许可 | 适配度 |
|---|---|---|---|---|---|---|
| **PawBench**（现选） | 通用任务（模型×harness 联合） | 150 题（Text 124 / MM 26） | 题内嵌 `grade()`（宿主 exec）+ LLM judge，hybrid 0.75 闸门 | Python 3.11+、Docker、JUDGE Key | Apache-2.0（NOTICE：129/150 题保留上游许可） | **强** |
| **PinchBench**（`pinchbench/skill`） | 通用任务（OpenClaw 场景） | v2.0 = 148 题 / 11 类 | automated（Python 函数）/ llm_judge / hybrid | 跑批需 OpenClaw CLI；**题目与 grader 本身零依赖** | **MIT** | **强** |
| **QwenClawBench** | 通用任务（真实用户分布） | 100 题 / 8 域（v1.1） | 规则 + 异常检测（基础设施故障显式标出） | 每题独立 Docker 容器、并行执行 | **MIT** | 中（题好，但官方跑法要每容器） |
| **SkillsBench** | Skills 专测 | v1.1 = 87 题 / 8 域 | 确定性 verifier（`verifier/` 目录）+ withheld oracle | BenchFlow + Docker + Modal；部分题需凭证/GPU | **Apache-2.0** | 中（重依赖题多，PawBench 只取了 15 题） |
| **Claw-Eval**（arXiv 2604.06132） | 端到端通用 agent | v3 摘要称 **300 题 / 9 类**（v1 时 104 题） | 规则引擎确定性（Completion/Safety/Robustness）+ LLM judge；**安全违规总分归零**；Pass@k 与 **Pass^k** | 15 个模拟企业服务 + Docker + 真实 Web 代理 | **MIT**（2026-09-19 一手核实 `claw-eval/claw-eval` 的 `LICENSE`；PyPI 同名包系另一个 153 题的 *ClawBench* 项目，与本仓库无关） | 中（判分设计最值得借鉴，宿主最重） |
| **WildClawBench** | 长程多模态（native runtime） | 60 题 / 6 类，双语 | hybrid：规则 + 环境状态审计 + LLM/VLM judge | Docker + 真实 CLI harness（OpenClaw/Claude Code/Codex/Hermes） | **MIT**（2026-09-19 一手核实） | 中 |
| **GAIA** | 通用助手 QA | 466 题（公开 dev 165 / 私有 test 300） | **规则式精确匹配**（无 LLM judge） | Docker 沙箱 + 联网（多数题需浏览） | dev 集可下但**明确要求不得转发**；test 答案私有 | 中→弱 |
| **GAIA2** | 动态/异步助手 | 【未证实】 | 【未证实】 | 【未证实】 | CC BY 4.0（代码 MIT）**【未证实：源自 AI Wiki 转述】** | 弱（未核实） |
| **SWE-bench / Verified / Lite / Multimodal** | 编码（repo 级 issue→patch） | 2294 / 500 / 300 / 517 | Fail-to-Pass 单测（容器内） | Docker，**120GB 磁盘 + 16GB RAM + 8 核**起 | 代码 MIT | 弱 |
| **SWE-Lancer** | 编码（自由职业） | 1488 题（公开 Diamond 502） | 端到端 Playwright 测试 / 管理者决策对比 | 统一 Docker 镜像 | CC BY 4.0（论文） | 弱 |
| **Terminal-Bench 2.0** | 终端任务 | **89 题 / 16 类**（3 难度档） | 每题 human oracle + 测试脚本校验容器终态，**二值 0/1** | 每题独立 Docker 容器；官方迁到 harbor | 【未证实】 | 中（契约形状接近，但需每容器） |
| **OSWorld / OSWorld-Verified** | 计算机使用（真人桌面） | 见原仓库 examples | 环境状态脚本判分 | VMware / VirtualBox / Docker+KVM / AWS；macOS 无 KVM | Apache-2.0 | 弱 |
| **WebArena** | Web（自托管 4 站） | 812 题 | 程序化判分（无 LLM judge） | 自建 4 个站点 + Playwright 浏览器 | 见仓库 | 弱 |
| **VisualWebArena** | Web（多模态） | 910 题（Classifieds 234 / Reddit 210 / Shopping 466） | 程序化 | 同 WebArena + 图像 | 见仓库 | 弱 |
| **WebVoyager** | Web（真实站点） | 643 题 / 15 站 | 人工 + GPT-4V 自动判分 | 真实公网 + 浏览器 | 【未证实】 | 弱（已近饱和） |
| **Mind2Web / Mind2Web 2 / Online-Mind2Web** | Web（离线轨迹 / 在线） | 2350 题 / 130 题 / 300 题 | 动作序列匹配 / **Agent-as-a-Judge** | 离线数据可跑；在线版需真实 web | 代码 MIT、数据集 CC BY 4.0；**test 集加密 + 禁止再分发** | 弱→中 |
| **AndroidWorld** | 移动 GUI | 116 题 / 20 app（随机参数化） | 持久 reward 信号 | Android 模拟器（2GB 内存 / 8GB 磁盘） | 见仓库（Google-Research） | 弱 |
| **WorkArena / WorkArena++** | 企业 Web | L1 19,912 实例 / 33 题；++ 682 题 | 程序化 validate（cheat 对照） | ServiceNow 实例（**HF 数据集 gated，需填表审批**）+ Playwright | 见仓库 | 弱（访问门槛） |
| **TheAgentCompany** | 数字员工 | **175 题** / 7 类角色 | checkpoint 部分分（确定性 + LLM 评） | Docker compose 全栈（GitLab/Plane/ownCloud/RocketChat）+ **30GB+ 磁盘 + host networking** | **MIT** | 弱 |
| **τ³-bench（原 τ²/tau2-bench）** | 客服对话 + 工具 | 域级（airline/retail/telecom/banking_knowledge） | `evaluation_criteria.actions` 动作比对（reward_basis 门控） | 无容器编排；需 **user simulator**（另起 LLM） | 见仓库 | 中（无容器是优点，但需多轮 user 模拟，非我们 CLI 形状） |
| **BFCL v4** | 函数调用（模型级） | 2k+ 题 | AST 匹配 + 可执行验证 + 其它 | Python CLI | 见仓库 | 中（不是 agent 级） |
| **API-Bank / ToolBench** | 工具调用 | 【未证实】 | 【未证实】 | 【未证实】 | 【未证实】 | 弱（本轮未取证，见 §五） |
| **Aider polyglot** | 编码（单仓编辑循环） | 225 题（Exercism，6 语言） | 单测 pass@2 + 编辑格式正确率 | Python + 各语言工具链 | 【未证实】 | 弱 |
| **LiveCodeBench** | 竞技编程 | v1 400 → v6 1,055+ | 单测 pass@1 | Python + 沙箱执行 | MIT（代码） | 弱（非 agent 级） |
| **MLE-bench** | ML 工程 | 75 竞赛（lite 低复杂度 22） | Any-Medal%（本地 grading 对比人类榜） | **3.3TB 数据（lite 158GB）+ GPU（约 24GB A10）** | 见仓库 | 弱（无 GPU） |
| **DABstep** | 多步数据分析 | 450+ 题 | **factoid 二值自动判分 + 混合打分算法** | 「只需代码执行环境」（官方明确对比 SWE-bench/MLE-bench 的简易性） | 数据集/论文 CC BY 4.0 | **中→强**（单域） |
| **CORE-Bench** | 科研复现 | 270 题（90 论文）；v1.1 39 题 + OOD 19 题 | 答案精确比对（report.json） | Docker，重依赖安装 | 【未证实】 | 弱 |
| **RE-Bench / HCAST（METR）** | 长程软件/ML 研究 | HCAST 189 题 / RE-Bench 7 题；**多数题不公开** | 任务专属评分脚本 | 需向 METR 申请；METR 现在用 UK AISI Inspect / Hawk | MIT（公开子集） | 弱（**故意不公开**：仅 31 题全公开） |
| **Vending-Bench / Vending-Bench Arena** | 长程商业模拟 | 【未证实】 | 【未证实】 | 【未证实】 | **Arena 不公开再分发** | 弱 |
| **PaperBench** | AI 研究复现 | 20 篇 ICML 2024 论文 / 8,316 可评分项 | 层级 rubric 的 LLM judge | 三容器流水线（建仓 / **GPU 执行** / judge） | 见 openai frontier-evals | 弱（GPU + 三容器） |
| **GDPval** | 经济价值任务 | 1,320 题（gold 开源 220） | LLM auto grader（**须上传 HF 并提交到 OpenAI 网站**） | 无需容器 | 【未证实】 | 弱（判分在 OpenAI 侧） |
| **BrowseComp** | 浏览检索 | 1,266 题 | LLM judge（或规则精确匹配回退） | **官方数据加密 CSV**，评测时解密 | 【未证实】 | 中→弱 |
| **AssistantBench** | Web 助手 | 214 题 | **全自动部分分**（字符串 F1 / 数值幅度 / 键值 F1） | 真实 web（gold 答案会随时间漂移） | 见仓库 | 中 |
| **AgentBench** | 多环境 agent | 8 环境 | 环境专属 | docker compose 多服务（webshop 需 ~16GB RAM） | 见仓库 | 弱（roadmap 已排除；2025 已转向 AgentRL 训练向） |
| **HAL（Holistic Agent Leaderboard）** | **元榜单/框架**（非基准） | 9 个基准 / 21,730 rollouts | 三维分析（模型/脚手架/基准）+ 成本 + 可靠性 | 需数百 VM 编排（——它自己就是编排层） | 见仓库 | 参考（不作题目源） |
| **ClawMark**（2026-04/05） | 多天多模态 coworker | 100 题 / 13 场景 | 规则式 + 加权分与严格成功率双报 | 动态环境（独立于 agent 变化） | CC BY 4.0（论文） | 弱 |
| **RealClawBench**（2026-06） | 真实开发者会话 | 281 题 | **确定性可验证 scorer** + 重建执行环境 | 重建环境 | 论文 CC BY 4.0；代码匿名 repo | 中（形状好，环境重建重） |
| **CocoaBench**（2026-04） | 统一数字 agent | 【未证实】（论文未给总数） | 「只看最终输出的自动评测函数」 | 需 vision/search/coding | Apache-2.0（自撰部分）**【未证实】**【推测：同上文另一论文体例】 | 中 |
| **Harbor-Index 1.0**（2026） | 元数据集 | 82 题（从 6,627 候选 / 54 基准蒸馏） | 沿用各源基准语义（harbor adapters） | 需 harbor 框架 | 见 hub | 参考 |
| **STATE-Bench**（微软，2026-05） | agent 记忆 | 450 题 / 3 域 | **确定性状态断言** + 用户体验 rubric | 无容器；需 user simulator + 有状态 DB | 开源 | 中（记忆维度，非我们契约） |

---

## 三、逐候选细节

### 3.1 PawBench（现选，复核通过）

- **是什么 / 维护者**：`agentscope-ai/PawBench`，隶属 OpenJudge 生态，作者署名为 "The OpenJudge Team"；README 徽章 `license-Apache%202.0`（<https://raw.githubusercontent.com/agentscope-ai/PawBench/main/README.md>）。引用是 `@misc` 不是论文——**工程产物，不是论文基准**（Citation 段，同上）。
- **许可**：仓库 Apache-2.0；`NOTICE` 明确写「PawBench reuses tasks from the following upstream agent benchmark suites. Each upstream task retains its original license」并列出 Claw-Eval / QwenClawBench / WildClawBench / PinchBench / skillsbench（<https://raw.githubusercontent.com/agentscope-ai/PawBench/main/NOTICE>）。→ **内部自跑无妨；公开携带题目时走「署名三件套」**（逐来源版权行 + 传播本 NOTICE + 逐题出处表，口径见 roadmap §六）。2026-09-19 更新：六路来源许可已全部一手核实 = 4 路 MIT（Claw-Eval / QwenClawBench / PinchBench / WildClawBench）+ 2 路 Apache-2.0（self-built / skillsbench），见 §3.5/§3.6。
- **规模与来源**：150 题（Text 124 + Multimodal 26），来源 = self-built 21 / claweval 52 / qwenclawbench 29 / pinchbench 23 / skillsbench 15 / wildclawbench 10（README「Tasks」段的 source 表；徽章 `tasks-150`、`models-9`、`harnesses-3`）。
- **任务格式与 grading 契约**：Markdown + YAML front-matter；`## Automated Checks` 内 Python 代码块，宿主 `exec()` 后取 `grade(transcript, workspace_path) -> dict`，**score = 返回 dict 中所有数值的算术平均**；`grading_type ∈ {automated, llm_judge, hybrid}`，hybrid 有 **0.75 惩罚闸门**（automated < 0.75 时 judge 贡献归零）。
  本节细节完全沿用 [`pawbench-harness-interface.md`](pawbench-harness-interface.md) §3.2/§3.4 的一手取证（其来源 S16/S17/S19），本轮未发现与上游矛盾之处，故不重复引用；README「Grading」段亦自述三种模式。
- **宿主要求**：Python 3.11+，**Docker**（官方跑法）；开发机 Windows → 依 [`pawbench-harness-interface.md`](pawbench-harness-interface.md) §3.5 的 local 逃生舱，走 WSL2/Docker Desktop。
- **能力覆盖**：五维标签（scenario / capabilities / complexity / modality / environment），capabilities 具体为 7 个（`Logic_Reasoning, Math_Computation, Code_Manipulation, Tool_Use, Skill_Use, Planning, Self_Verification`）。README 自述 `Skill_Use` 均分 47.2、skillsbench 源均分 40.9、text 74.1 vs multimodal 64.0、closed 72.9 vs open 68.9。
- **饱和/污染状态**：v1.0 于 2026-06 发布（Citation `month = {06}, year = {2026}`），**远未饱和**；但它是「复用已有题」，`qwenclawbench` / `pinchbench` 等上游本身公开可下，**存在被训练语料吸收的长期风险**（【推测】，上游未声明豁免）。
- **已知阻塞**：① 上游许可（见上）；② 官方 judge 默认 claude-opus 类别模型，换 judge = 换尺子（[`pawbench-harness-interface.md`](pawbench-harness-interface.md) §六.3）；③ 无 resume（同上 §3.6）。
- **适配度：强。** 一句话：**契约形状匹配度是全场最高，没有理由推翻。**

### 3.2 PinchBench（本轮最重要的新增建议）

- **是什么 / 维护者**：`pinchbench/skill`，由 Kilo Code 团队（Brendan O'Leary）制作，目标是「给 OpenClaw 选模型」；站点 <https://pinchbench.com/about>，仓库 <https://github.com/pinchbench/skill>。
- **许可**：**MIT**（`LICENSE` 原文 "MIT License / Copyright (c) 2026 PinchBench"，<https://raw.githubusercontent.com/pinchbench/skill/main/LICENSE>）。→ 这是它相对 PawBench 聚合层的**决定性优势**：同一套任务格式，但分发许可干净。
- **规模与版本**：站点当前口径「**147 tasks across 11 categories**，matching `pinchbench/skill` 的 `tasks/manifest.yaml`」（<https://pinchbench.com/about>）；作者博客称 2.0 版为 148 题（<https://blog.kilo.ai/p/pinchbench-20-is-here>，作者本人发布，非第三方）。**两个数字都以官方口径为准，存在 147/148 的口径差**（【未证实】哪一个是最新 manifest）。
- **任务格式与 grading**：与 PawBench **同源同形**——「markdown files with YAML frontmatter」+ `Prompt` / `Expected Behavior` / `Grading Criteria`（原子 checklist）/ `Automated Checks`（Python 函数，按 workspace files + transcript 判分）/ `LLM Judge Rubric`（<https://pinchbench.com/about>）。站点任务清单直接标注每题的 grading 类型（automated / llm_judge / hybrid），例如 `task_sanity` / `task_calendar` / `task_todo_list_cleanup` / `task_cron_organizer` 为 automated，`task_email_triage` 为 hybrid（<https://pinchbench.com/about>）。
- **评分机制**：automated = Python 函数；llm_judge = rubric；hybrid = 两者。v2 起默认 judge **换成 Haiku** 并加 judge 结果缓存、支持 thinking-level 分档、`new_session: true` 多轮隔离（<https://blog.kilo.ai/p/pinchbench-20-is-here>）。
- **宿主要求**：**题目与 grader 本身零依赖**（纯 markdown + Python）；官方跑批需要 OpenClaw CLI（这不是我们要的）。→ 我们只需要它的**题面 + `Automated Checks` 函数**，与我们现有 runner 完全同构。
- **能力覆盖**：11 类含 Productivity / Research / Writing / Coding / Data analysis / Log analysis / Image & PDF 等（站点分类清单，<https://pinchbench.com/about>）。能力切片没有 PawBench 那样规整的 7 轴标签——**这是它的短板**（要自己做标签映射）。
- **饱和/污染**：v2.0 于 2026-05-11 发布，**未饱和**；作者已修掉「95% 失败率的不可解题」与若干评分可游戏性问题（<https://blog.kilo.ai/p/pinchbench-20-is-here>）。
- **已知阻塞**：【未证实】部分题依赖真实 web/时间（如 `task_stock`、`task_deep_research`、`task_events`），gold 答案会随时间漂移；需在切片时排除。
- **适配度：强。** 一句话：**它是 PawBench 任务格式的原始出处，许可更干净、题面完全同构，应作为 M2 切片的第一个取材池。**

### 3.3 QwenClawBench

- **是什么 / 维护者**：`SKYLENAGE-AI/QwenClawBench`（阿里数据 + Qwen 生态），自述「originally built as an internal benchmark during the development of Qwen3.6-Plus, and has since been optimized and open-sourced」（<https://raw.githubusercontent.com/SKYLENAGE-AI/QwenClawBench/main/README.md>）。
- **许可**：徽章 `license-MIT-green`，指向仓库 `LICENSE`（同上 README）。
- **规模**：**100 tasks / 8 core domains，version 1.1**；HF 数据集 `skylenage-ai/QwenClawBench`；榜单 <https://skylenage-ai.github.io/QwenClawBench-Leaderboard/>（同上）。
- **宿主要求**：**每题 dedicated Docker container + 并行执行 + 异常检测**（README「Why QwenClawBench?」明确列出 Docker Isolation / Concurrent Execution / Anomaly Detection）。
- **评分**：规则判分 + 基础设施故障显式打标（不静默折进分数）——**这一点与我们在 [`pawbench-harness-interface.md`](pawbench-harness-interface.md) §3.4 借来的「异常检测」思路一致**。
- **适配度：中。** 题 100 道、MIT、真实用户分布，是好池子；但官方跑法要每题容器，**我们要的是它的题，不是它的 runner**。PawBench 已从中取了 29 题——可直接从上游多取。

### 3.4 SkillsBench

- **是什么 / 维护者**：`benchflow-ai/skillsbench`，SkillsBench Team（含 Dawn Song 为 advising author），论文 arXiv:2602.12670，站点 <https://www.skillsbench.ai/>，v1.1 发布于 2026-06-16（<https://www.skillsbench.ai/blogs/skillsbench-1-1>）。
- **许可**：**Apache License 2.0**（`LICENSE` 原文 <https://raw.githubusercontent.com/benchflow-ai/skillsbench/main/LICENSE>）。
- **规模**：v1.1 = **87 native BenchFlow `task.md` 包 / 8 域**，每包含 `environment/`、`oracle/`、`verifier/` 三个目录；HF 镜像 + leaderboard 数据集（blogs 页）。
- **评分机制**：**确定性 verifier**（`verifier/` 目录）+ 隐藏 oracle；每题跑 no-Skills / curated-Skills / self-generated-Skills 三条件配对评测（论文与 blogs 页一致）。
- **论文关键数字**（一手）：v1.1 论文报告 **87 题 / 8 域 / 18 个 model-harness 配置**；curated Skills 把平均 resolution rate 从 **33.9% 提到 50.5%（+16.6 pt）**（blogs 页与 arXiv 摘要一致；arXiv 早期版本口径为 86 题 / 11 域 / +16.2pt，说明论文在迭代）。
- **宿主要求**：BenchFlow 框架 + Docker；默认云执行用 **Modal**；**credential-dependent / integration-incompatible 的包被放到 `tasks-extra/` 并从默认 roster 排除**（blogs 页）→ 这解释了 PawBench 只取了 15 题。
- **适配度：中。** 是我们 `Skill_Use` 维度（roadmap §三 P1-A 的验收支撑）最直接的题源，但**重依赖题比例高**，需像 PawBench 一样筛「零依赖子集」；论文亦印证 SkillsBench 与 OpenCode 上跑 78 题自包含子集的做法（Qwen3.6 blog 脚注：`SkillsBench: Evaluated via OpenCode on 78 tasks (self-contained subset, excluding API-dependent tasks)`，<https://qwen.ai/blog?id=qwen3.6-35b-a3b>）。

### 3.5 Claw-Eval（判分设计最值得借鉴）

- **是什么 / 维护者**：北京大学（State Key Lab of Multimedia Information Processing）+ 香港大学；论文 arXiv:2604.06132v3，项目页/榜单 <https://claw-eval.github.io>；PawBench NOTICE 指向 `https://github.com/claw-eval/claw-eval`（<https://raw.githubusercontent.com/agentscope-ai/PawBench/main/NOTICE>）。
- **规模（有版本演进，注意引用版本）**：v3 摘要为 **300 human-verified tasks / 9 categories / 3 groups**（general service orchestration、multimodal perception and interaction、multi-turn professional dialogue），共 **2,159 条细粒度 rubric 项**（arXiv:2604.06132v3）；中文媒体报道口径为 **104 题**（<https://jigou.jiqizhixin.com/articles/2026-03-18-8>，属二手，仅用于说明早期规模，不作引用依据）。
- **评分机制（本轮最有价值的部分）**：三条独立证据通道（execution traces / service-side audit logs / post-execution environment snapshots），评分维度 = **Completion + Safety + Robustness**，报 **Average Score / Pass@k / Pass^k（三次全胜）**；论文核心结论之一：trajectory-opaque 评测**漏检 44% 的安全违规与 13% 的鲁棒性失败**，且注入故障后 Pass@3 基本不变而 **Pass^3 最多掉 24pt**（arXiv:2604.06132v3）。
- **宿主要求**：15 个模拟企业服务（Gmail/日历/Todo/财务/工单等）+ 真实 Web + Docker 终端环境 + 随机错误注入（429/500/2-4s 慢响应）（<https://jigou.jiqizhixin.com/articles/2026-03-18-8>，二手；一手见论文 §3 与附录 B）。
- **许可**：**MIT**（一手核实 2026-09-19：`claw-eval/claw-eval` 的 `LICENSE` 原文 "MIT License / Copyright (c) 2026 claw-eval"，<https://raw.githubusercontent.com/claw-eval/claw-eval/main/LICENSE>；无 `NOTICE`，README 无任务数据使用/转发限制，仅要求引用论文）。**命名碰撞已排除**：PyPI 上 `claw-eval`（别名 `clawbench-eval`，包内自述 **ClawBench: 153 题 / 144 live websites / 15 类**，License 段写 "Apache 2.0 -- see LICENSE"）描述的是**另一个项目**（<https://pypi.org/project/claw-eval/>），与 PawBench 所引仓库无关。
- **适配度：中。** 一句话：**题不要（宿主太重），但要它的判分协议——Pass^k 与安全一票否决，是唯一能触及我们护城河（确认闸门/熔断/回滚）的外部设计。**

### 3.6 WildClawBench

- **是什么 / 维护者**：上海 AI Lab / CUHK 等（Shuangrui Ding 等），论文 arXiv:2605.10912v1，代码 `github.com/internlm/WildClawBench`（PawBench NOTICE 亦指向 `https://github.com/InternLM/WildClawBench`），站点 <https://internlm.github.io/WildClawBench/>（更新于 2026-07-20）。
- **规模**：**60 题 / 6 类，双语、多模态**，平均每题约 8 分钟墙钟、**20+ 次工具调用**；跑在可复现 Docker 容器里的**真实 CLI harness**（OpenClaw / Claude Code / Codex / Hermes）（arXiv 摘要）。
- **评分**：hybrid —— 确定性规则检查 + 环境状态副作用审计 + LLM/VLM judge（arXiv 摘要）。
- **一手数据**：最佳 Claude Opus 4.7 在 OpenClaw 下 **62.2%**，其余均 <60%；**仅换 harness 就能让同一模型移动最多 18 分**（arXiv 摘要）；站点榜（更新 2026-07-20）Top = GPT-5.6 Sol **67.2%**、Claude Opus 4.8 **64.7%**（<https://internlm.github.io/WildClawBench/>）。
- **许可**：**MIT**（一手核实 2026-09-19：`InternLM/WildClawBench` 的 `LICENSE` 原文 "MIT License / Copyright (c) 2026 WildClawBench"，<https://raw.githubusercontent.com/InternLM/WildClawBench/main/LICENSE>；无 `NOTICE`、无转发限制。备注：数据托管在 HF datasets，部分题依赖第三方 YouTube 下载——其题若进公开集需单独标注）。
- **适配度：中。** 题好且**双语**（对我们中文语料友好），但要求真实 CLI harness + Docker；PawBench 只取了 10 题。

### 3.7 GAIA / GAIA2

- **是什么**：Meta FAIR + HuggingFace + AutoGPT，arXiv:2311.12983（ICLR 2024），466 题；**答案对 300 题保留**以驱动榜单（论文原文："We release our questions while retaining answers to 300 of them to power a leader-board"，<https://arxiv.org/pdf/2311.12983v1.pdf>）。
- **评分**：**规则式精确匹配**，「no partial credit, no subjective grading」；官方榜单口径为「best run」，非多跑均值（榜单页 <https://huggingface.co/learn/agents-course/zh-CN/unit4/what-is-gaia> 引官方榜单说明）。开源实现如 EvalScope 亦注明 "Rule-based scorer ported verbatim from the official GAIA leaderboard (**no LLM judge**)"，并默认 `python:3.11` Docker 沙箱 + 联网（<https://evalscope.readthedocs.io/en/latest/benchmarks/gaia.html>）。
- **可用的公开切片**：validation 集 **165 题**（level1 53 / level2 86 / level3 26），答案公开（同上；官方 HF 数据集 `gaia-benchmark/GAIA`）。
- **许可与阻塞**：公开 dev 集**明确要求「Please do not repost the public dev set, nor use it in training data」**（官方榜单页原文）；test 答案私有。ModelScope 镜像标 Apache-2.0（<https://www.modelscope.cn/datasets/AI-ModelScope/GAIA>），**HF 原始数据集页面本轮抓取失败（HTTP 500），许可【未证实】**。
- **饱和度**：**已显著饱和** —— 2026-06 期间领先系统在 validation 上已报 >92%，且官方已发布后继 **GAIA2**（<https://aiwiki.ai/wiki/gaia_benchmark>，**属二手转述，标【未证实】**）。
- **适配度：中→弱。** 判分「确定性、零 judge、零 LLM 成本」很诱人，但 ① 答案是短 QA 不是产物落盘，与我们的 `--dir` 产物契约不同；② 多数题需真实联网（gold 漂移）；③ dev 集不得转发 + test 私有 → **公开我们自己的分数受约束**；④ 已饱和。→ 维持 roadmap「GAIA 留二期」的判断。

### 3.8 SWE-bench 系列 / SWE-Lancer / Terminal-Bench / Aider / LiveCodeBench

- **SWE-bench**：2294 实例、12 个 Python 仓库，每题一个 Docker 镜像；评测靠 **Fail-to-Pass 测试**（官方页 <https://swe-agent-bench.github.io/original.html>）。官方 harness 明确要求「**x86_64 机器、≥120GB 空闲磁盘、16GB RAM、8 CPU 核**」（<https://pypi.org/project/swebench/3.0.5/>）。许可 MIT（同上）。**适配度：弱**（磁盘 + 每题镜像 + 编码专域）。
- **SWE-bench Verified / Lite / Multilingual / Multimodal**：500 / 300 / 300 / 517 实例；官方用 **mini-SWE-agent** 统一 harness 评测（<https://www.swebench.com/>）。**【未证实】** 2025-09 起 OpenAI 宣布不再用 Verified 评测（来自 codesota 编者按，二手，<https://www.codesota.com/benchmark/livecodebench>）。
- **SWE-Lancer**：1488 题（764 IC / 724 Manager），$1M 奖池，公开 Diamond 502 题；IC 题用 **Playwright 端到端测试**判分，Manager 题对比真实工程经理决策（arXiv:2502.12115v3）；仓库 `openai/SWELancer-Benchmark`（【未证实】已归档并并入 `openai/preparedness`，该说法来自 AI Wiki 二手）。
- **Terminal-Bench 2.0**：**89 题 / 16 类 / 3 难度档**，每题独立 Docker 容器 + human-written oracle + 校验容器终态的测试脚本，**二值 0/1 判分**（<https://www.tbench.ai/leaderboard/terminal-bench/2.0>；分类计数见 <https://snorkel.ai/leaderboard/terminal-bench-2-0/>）。README 提示新用户改走 **harbor** 框架（<https://raw.githubusercontent.com/laude-institute/terminal-bench/main/README.md>）。**适配度：中**——「单条指令 → 终态校验」的形状与 headless 契约很接近，但要求每题容器，且是终端/编码专域。
- **Aider polyglot**：225 题（Exercism 6 语言中最难的一批），**pass@2** + 编辑格式正确率（作者原帖 <https://aider.chat/2024/12/21/polyglot.html>；仓库 <https://github.com/Aider-AI/polyglot-benchmark>）。**适配度：弱**（模型级 + 需 Aider 编辑循环）。
- **LiveCodeBench**：v1 400 题 → v6 1,055+ 题，按**发布日期做时间窗**去污染（arXiv:2403.07974；<https://livecodebench.github.io/>）。代码 MIT（NVIDIA 打包元数据 <https://pypi.org/project/nvidia-livecodebench/26.3/>）。**适配度：弱**（单文件算法题，SOTA 已 91.7%+）。
- **共同结论**：这一组**全部是「编码/终端专域 + 每题容器 + 部分需 GPU」**，与 roadmap §一「A 线：通用任务 agent，不转编码 agent」的路线决策冲突。

### 3.9 计算机使用类（OSWorld / WebArena / VisualWebArena / WebVoyager / AndroidWorld / Mind2Web / WorkArena / TheAgentCompany）

- **OSWorld / OSWorld-Verified**：Apache-2.0；需 VMware/VirtualBox（macOS 不支持 KVM，须用 VMware）或 Docker+KVM / AWS / Modal / Daytona；**要上官方榜必须「schedule a meeting with us」并让你在我们侧跑**（<https://raw.githubusercontent.com/xlang-ai/OSWorld/main/README.md>）。→ **发布阻塞 + 宿主阻塞。**
- **WebArena**：812 题、自托管站点（SHOPPING / REDDIT / GITLAB / MAP / WIKIPEDIA 等）+ Playwright；仓库自述「canonical implementation」，现代用法建议走 AgentLab/BrowserGym（<https://raw.githubusercontent.com/web-arena-x/webarena/main/README.md>）。**适配度：弱。**
- **VisualWebArena**：910 题（Classifieds 234 / Reddit 210 / Shopping 466），GPT-4V 首发仅 **16.37%** vs 人类 88.7%（<https://aiwiki.ai/wiki/visualwebarena/raw>，二手汇总；一手为 ACL 2024 论文）。**适配度：弱。**
- **WebVoyager**：643 题 / 15 站；首发 GPT-4V agent **59.1%**，2026 年顶尖商业 agent 已 **97-98%**（饱和）（<https://aiwiki.ai/wiki/webvoyager/edit> 与 <https://www.awesomeagents.ai/leaderboards/web-agent-benchmarks-leaderboard/>，均二手）。**适配度：弱（已饱和 + 真实公网）。**
- **AndroidWorld**：116 题 / 20 app，动态参数化；需 Android 模拟器，轻量（约 2GB 内存 / 8GB 磁盘）（<https://raw.githubusercontent.com/google-research/android_world/main/README.md>）。**适配度：弱（GUI）。**
- **Mind2Web / Mind2Web 2 / Online-Mind2Web**：2350 题（137 站 / 31 域）；**代码 MIT、数据集 CC BY 4.0**，但 **test split 加密分发 + 原文禁止再分发**（"Please DO NOT redistribute the unzipped data files online"，<https://git.durrantlab.pitt.edu/Jaimla/Mind2Web>）。Mind2Web 2 = 130 题、**Agent-as-a-Judge**、130 题私有 test 120 题（<https://osu-nlp-group.github.io/Mind2Web-2/>）。**适配度：弱→中（离线轨迹可跑，但在线版需真实 web；test 集不可转发）。**
- **WorkArena / WorkArena++**：WorkArena-L1 = 33 题 / 19,912 实例，++ = 682 题；**需 HF gated 数据集（填表 + 审批）** + ServiceNow 实例 + Playwright（<https://raw.githubusercontent.com/ServiceNow/WorkArena/main/README.md>）。**适配度：弱（访问门槛）。**
- **TheAgentCompany**：**175 题**（SDE/PM/DS/HR/Finance/Admin 等 7 类），**MIT**（<https://raw.githubusercontent.com/TheAgentCompany/TheAgentCompany/main/README.md>）；评测 = checkpoint 部分分（确定性 evaluator + LLM evaluator 混合）；环境 = 自托管全栈 Docker compose（GitLab/Plane/ownCloud/RocketChat），**需要 30GB+ 空闲磁盘 + host networking**，Windows 走 `setup.bat`（README Quick Start）。最佳 agent（Gemini-2.5-Pro）自动完成 **30.3%** 任务、部分分 **39.3%**，平均 **$4.2/题**（<https://openreview.net/forum?id=LZnKNApvhG>，NeurIPS 2025 D&B poster）。**适配度：弱（30GB + 全栈 compose + host networking；Windows 上尤其脆）。** 它的 checkpoint 部分分思路值得借，但环境太重。

### 3.10 工具/函数调用与垂直基准（τ³ / BFCL / DABstep / CORE-Bench / MLE-bench / RE-Bench·HCAST / PaperBench / GDPval / BrowseComp / Vending-Bench / STATE-Bench）

- **τ³-bench（`sierra-research/tau2-bench`）**：客服域（`mock / airline / retail / telecom / banking_knowledge`），文本半双工 + 语音全双工 + 知识检索；评分 = `evaluation_criteria.actions` 动作比对（`reward_basis` 门控 reward）；**注意 v1.0.1 变更判分导致分数不可跨版本比较，且 75+ 题被修**（<https://raw.githubusercontent.com/sierra-research/tau2-bench/main/README.md>）。**优点：无需容器编排。缺点：必须起 user simulator（另一路 LLM）且是多轮对话形状，与我们「单 prompt → 产物」契约不符。适配度：中。**
- **BFCL v4**：UC Berkeley Gorilla 团队；2k+ 题目、多语言、含 multi-turn / live / relevance detection / web search / memory / format sensitivity；对比表列出模型 License（如 GLM-4.6 = MIT）（<https://gorilla.cs.berkeley.edu/leaderboard>）。**它是模型级函数调用榜，不是 agent 级。适配度：中。**
- **DABstep**：Adyen + HuggingFace，arXiv:2506.23719（CC BY 4.0），**450+ 题**，**factoid 二值自动判分**（"binary outcome, right or wrong, without interpretation"）；官方明确把「**只有 450+ 真实任务 + 简单设置**、只需一个代码执行环境」列为对 SWE-bench / MLE-bench 的差异化优势（<https://www.adyen.com/knowledge-hub/data-agent-benchmark-for-multi-step-reasoning-dabstep>）。最佳 agent 在 hardest 题上仅 **14.55%**（arXiv 摘要）。数据集 <https://huggingface.co/datasets/adyen/dabstep>。**适配度：中→强**——判分确定、无容器编排、成本极低；**代价是单域（金融数据分析）+ 需下载数据文件**。
- **CORE-Bench**：270 题 / 90 篇论文 / 3 学科（arXiv:2409.11363）；v1.1 收窄为 **39 题** + OOD 19 题，因为 accuracy 已饱和，作者转向测**可靠性 / 效率 / model-vs-scaffold / 人机协作 uplift**（arXiv:2606.26158）。**适配度：弱**（Docker + 重依赖安装；准确率维度已饱和）。
- **MLE-bench**：75 Kaggle 竞赛（low 22 / medium 27 / high 26），**数据 3.3TB（lite 158GB）**；官方基准配置 **36 vCPU / 440GB RAM / 1×24GB A10 GPU**、24 小时运行（<https://raw.githubusercontent.com/openai/mle-bench/main/README.md>）。**适配度：弱（无 GPU + TB 级数据）。**
- **RE-Bench / HCAST（METR）**：HCAST **189 题 / 78 家族**，人类基线 140 位专家 563 次尝试；**METR 只放出 11 个示例任务家族，其余刻意不公开以防污染**（arXiv:2503.17354）；公开侧仅「**31 fully public tasks**」另加 100 题的摘要（<https://metr.org/measuring-autonomous-ai-capabilities/>）。**适配度：弱（不可作为基准，因为题不给你）。**
- **PaperBench**：20 篇 ICML 2024 Spotlight/Oral 论文、**8,316 个可单独评分项**；三阶段流水线 = 建仓容器 → **有 GPU 的执行容器** → judge 容器；最佳 agent（Claude 3.5 Sonnet + 开源脚手架）**21.0%**，人类 ML PhD **41%**（<https://openreview.net/forum?id=xF5PuTLPbn> / <https://ukgovernmentbeis.github.io/inspect_evals/evals/coding/paperbench/>）。**适配度：弱（GPU + 三容器）。**
- **GDPval**：44 个职业、全量 1,320 题（**gold 开源 220 题**）；判分必须「**save results → upload to HuggingFace → submit to OpenAI auto grader**」（<https://ukgovernmentbeis.github.io/inspect_evals/evals/assistants/gdpval/>）。**适配度：弱（判分在第三方侧，无法自建可复现尺子）。**
- **BrowseComp**：1,266 题，需要持久浏览；**官方数据是加密 CSV，评测时解密**；判分 = LLM judge（可回退规则精确匹配）（<https://evalscope.readthedocs.io/en/latest/benchmarks/browsecomp.html>；一手源码 <https://github.com/openai/simple-evals/blob/main/browsecomp_eval.py>）。**适配度：中→弱。**
- **Vending-Bench**：单机长程模拟（>20M tokens/run），评分基于资产/利润，方差极大（arXiv:2502.15840）；后继 **Vending-Bench Arena 不公开再分发**（arXiv:2608.14825v1 明示 "maintained by Andon Labs and not publicly redistributed"）。**适配度：弱（不可得）。**
- **STATE-Bench（微软，2026-05）**：客户支持/旅行/购物 3 域、**450 题**，**确定性状态断言**判分 + 用户体验 rubric；需 user simulator + 有状态 DB（<https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/>）。**适配度：中（记忆维度好，契约不同）。**

### 3.11 2026 新基准与元榜单（本轮新发现，值得记名）

- **HAL（Holistic Agent Leaderboard）**：Princeton SAgE，arXiv:2510.11977，ICLR 2026；**是框架/榜单不是基准**：统一 harness + 数百 VM 编排 + 成本追踪 + agent log 分析；跑了 **21,730 rollouts / 9 模型 / 9 基准，约 $40,000**，覆盖 coding（SWE-bench Verified、CORE-Bench Hard、USACO、SciCode、ScienceAgentBench）、web（AssistantBench、GAIA、Online Mind2Web）、客服（TAU-bench Airline）等（<https://arxiv.org/pdf/2510.11977v1>；榜单 <https://hal.cs.princeton.edu/>）。**关键可用结论**：① agent 可以贵 100× 而只高 1%；② 多跑一致性下成绩会从 60% 掉到 25%；③ 更高 reasoning effort 在多数 run 里**降低**准确率（同 arXiv 摘要；②来自 <https://ai-tldr.dev/releases/hal-holistic-agent-leaderboard-iclr-2026/> 的转述，**属二手，需回一手核**）。
- **Harbor / Harbor-Index**：Laude Institute 的 harbor 框架已成为 Terminal-Bench 2.0 的推荐执行层，并提供 54 个基准的 **adapters**；Harbor-Index 1.0 是从 6,627 候选蒸馏出的 **82 题**元数据集，**没有 agent-model 组合能过 30%**（<https://www.tbench.ai/news/harbor-index>）。→ **如果哪天真要「多基准接入 + 统一契约」，harbor adapters 是比自建 runner 更成熟的路线（本轮只记名，不建议现在动）。**
- **RealClawBench**：北大 + Qiyuan Tech，arXiv:2606.03889，**281 题**，从真实 OpenClaw 会话重建执行环境 + **确定性可验证 scorer**；最好系统仅解 **65.8%**（arXiv 摘要）。**形状与我们的「单 prompt → 产物 → 确定性判分」最接近的新工作之一**，但「重建执行环境」提示环境仍重；代码在匿名 repo（4open.science）。
- **ClawMark**：evolvent-ai，arXiv:2604.23781，**100 题 / 13 场景 / 多天多模态**，规则式判分，双报「加权分 + 严格成功率」；环境**动态变化**（agent 运行期间外部世界独立变化）。**适配度：弱（多天 + 动态环境），但「加权分 vs 严格成功率双报」的口径值得借。**
- **CocoaBench**：arXiv:2604.11201v2，**任务只由「一条指令 + 对最终输出的自动评测函数」指定**——**这正是我们的契约形状**；最好系统仅 **45.1%**（arXiv 摘要）。**未核实题数与许可**，列入 §五。
- **HCAST / 时间视野（METR）**：作为驾驭「长程能力」叙事的框架继续被引用；**2026-01 的 Time Horizon 1.1 把任务扩到 228 题**（<https://tekai.dev/catalog/hcast>，二手，**未证实**）。

---

## 四、对我们 harness 契约的适配分析

> **纪律**：本节只**标记**「若采纳会触碰到 [`pawbench-harness-interface.md`](pawbench-harness-interface.md) 里已冻结的接口」的点，**不假设接口可以改**。任何一条真要落地，都必须单独开 issue 走冻结窗口。

| # | 外部设计 | 会触碰我们哪个冻结项 | 标记 |
|---|---|---|---|
| 1 | **Pass^k / 多跑一致性**（Claw-Eval 的 `Pass^k`、HAL 的「60%→25% 一致性衰减」） | 评测台 **batch manifest schema**（现为「一次/题 + `git rev-parse HEAD`」）需扩为「N 次/题 + seed + 一致性列」 | ⚠️ **标记：manifest 契约** |
| 2 | **三证据通道**（Claw-Eval：execution traces + service-side audit logs + environment snapshots；ClawMark 同类） | 我们只有 `data/traces/<thread_id>.jsonl` 一个通道；adapter 的 `extract_transcript()` 契约（[`pawbench-harness-interface.md`](pawbench-harness-interface.md) §3.3 八条）会不够用 | ⚠️ **标记：trace/可观测契约** |
| 3 | **安全一票否决 / Robustness 维度**（Claw-Eval 的核心） | 结果 JSON 需新增 safety/robustness 维度；且错误注入要求 runner 能主动注入 429/500/慢响应 → **会碰到 `resilience.py` 冻结区**（用真实故障注入去测熔断 = 直接对着冻结文件做验证） | ⚠️ **标记：结果 schema + 冻结区**（后者尤其要问人） |
| 4 | **`grade(transcript, workspace_path) -> dict` vs「对最终输出的自动评测函数」**（CocoaBench / RealClawBench 式） | §3.4 的判分签名是冻结点；换签名 = 换契约 | ⚠️ **标记：`grade()` 签名** |
| 5 | **judge 模型选择**（PawBench 默认 claude-opus 类；PinchBench v2 默认 Haiku；我们计划 qwen3.8-max-0902） | [`pawbench-harness-interface.md`](pawbench-harness-interface.md) §六.3 已判定「换 judge = 换尺子」；本轮**新增证据**：多个基准各自换了默认 judge（PawBench=opus 类、PinchBench v2=Haiku），说明「judge 口径必须在报告里显式声明」应升级为硬规则 | ✅ **不需要改接口，但报告口径必须写死** |
| 6 | **harbor adapters / AgentAdapter 接口**（Terminal-Bench 2.0、Harbor-Index） | 若采纳，会**整体替换**我们当前的「CLI 单发」契约（`-p ... --dir ... --output json`） | ⚠️ **标记：整体契约替换，成本最高，不建议现在动** |

**结论：§四 的 6 条里，5 条是「标记但不动」。唯一可以立刻做且不触碰任何冻结项的是 #5 的口径硬化（在报告里强制写明 judge 模型与切片来源）。**

---

## 五、推荐与理由 + 风险 / 未证实清单

### 5.1 推荐（按执行顺序）

1. **M1 链路验证（现在这步）不改素材** —— 仍从 PawBench 抽 10–20 道「纯文本 + 封闭 + 无素材 + 零依赖」题，验证 `grade()` 硬分 + judge 软分 + 产物/trace 落盘可回放的**链路完整性**（roadmap §四 M1 验收不变）。
2. **M2 扩规模时改取材口径** —— 不再「以 PawBench 目录为主」，改为**按上游仓库直接取材**，优先级：
   - ① `pinchbench/skill`（**MIT**，格式同构，automated 题最多）→ 先把 automated（非 llm_judge）子集吃干净；
   - ② `QwenClawBench`（**MIT**，100 题，真实用户分布）；
   - ③ `benchflow-ai/skillsbench`（**Apache-2.0**，但只取 `tasks/` 下非 `tasks-extra/` 的自包含题，对齐 SkillsBench 论文说的「78 题自包含子集」口径）；
   - ④ `WildClawBench`（**MIT**，已核实，题好且双语）。
   **理由**：同一套题目，走上游 = 许可清晰 + 拿到上游的版本标签 + 不被 PawBench 的 `labels:` 嵌套口径问题牵连（该问题见 [`pawbench-harness-interface.md`](pawbench-harness-interface.md) §六.4-6）。
   > **2026-09-19 补记**：六路来源许可已全部一手核实（4 路 MIT + 2 路 Apache-2.0，见 §3.5/§3.6、§5.2-1），「许可清晰」不再构成上游相对聚合层的差异；**取材口径已决议（同日后续，评测台 spec 筹备）**：PinchBench 暂不接入，M1/M2 题源写死 PawBench 切片，原「触发式补充」建议暂缓（启用时走记录修正）。（搬运/署名的处理口径同日已记入 roadmap §六）。
3. **M3 定向修复的验收仍是「同切片前后对比」** —— 本轮新增两条**报告口径硬规则**：① 必须写明 judge 模型（换 judge 不可比）；② 必须写明每题来自哪个上游仓库 + 该仓库 commit/版本。
4. **二期（不进当前里程碑，只记名）** —— 若要做「运行时深度」叙事，借鉴 **Claw-Eval 的 Pass^k + 安全一票否决**与 **HAL 的可靠性/成本维度**当**自建协议的维度**，**不整套接入**它们的容器编排。

**为什么不换主基准（一句话收口）**：本轮全表 30+ 个候选里，**没有任何一个同时满足**「题面是文本 + 每题自带确定性 `grade()` + 宿主只需 Python/Docker 不做每题容器 + 许可允许我们公开自己的分数 + 覆盖 file/tool/planning/self-verification 而非纯编码或纯 GUI」。PawBench 是这个交集里唯一的成员；PinchBench 是这个交集的**更干净的上游**。

### 5.2 风险清单

| # | 风险 | 影响 | 状态 |
|---|---|---|---|
| 1 | **PawBench NOTICE：129/150 题各留原许可** | 内部自跑无妨；公开携带题目按「署名三件套」处理（见 roadmap §六） | ✅ **已降级**（2026-09-19：六路来源许可全部一手核实 = 4 路 MIT + 2 路 Apache-2.0；残留 = 个别题内嵌第三方素材，发布前抽查） |
| 2 | **判分依赖宿主重依赖**（`cvxpy` / `unified-planning` / `up-pyperplan` 等） | 缺依赖 → `GRADING_SCRIPT_ERROR` → 静默吃 0 分 | ✅ 已在 [`pawbench-harness-interface.md`](pawbench-harness-interface.md) §六.4-4 记录，本轮无变化 |
| 3 | **PinchBench 147 vs 148 题数口径差** | 影响切片分母，不影响方法 | 【未证实】 |
| 4 | **部分 PinchBench 题依赖真实 web / 时间**（`task_stock`、`task_deep_research`、`task_events` 等） | gold 会随时间漂移 → 不可复现 | 【推测】，需静读每个 task 文件确认 |
| 5 | **Claw-Eval 命名碰撞**（PyPI `claw-eval` 包描述的却是 153 题的 *ClawBench*） | 若误引会造成许可与规模张冠李戴 | ✅ **已解除**（2026-09-19 一手核实：`claw-eval/claw-eval` 存在且 **MIT**；两者确系不同项目） |
| 6 | **WildClawBench 许可未取证** | 若上游无明确许可，公开我们的分数有风险 | ✅ **已解除**（2026-09-19 一手核实为 **MIT**，`InternLM/WildClawBench` `LICENSE`） |
| 7 | **GAIA dev 集不得转发 + test 答案私有；HF 数据集页抓取失败** | 公开自跑分数受约束；数据集许可未能一手确证 | 【未证实】（ModelScope 镜像标 Apache-2.0，但那是镜像） |
| 8 | **GAIA 已明显饱和**（2026-06 领先系统 validation >92%） | 作验收锚点的区分度不足 | 【未证实】（转述自 AI Wiki） |
| 9 | **多数「更贴形状」的新基准宿主都重**：RealClawBench（重建执行环境）、CocoaBench（需 vision/search/coding）、ClawMark（多天动态环境） | 二期若要接入，工作量大 | 【推测】 |
| 10 | **运行时深度（checkpoint/resume/rollback/confirm-gate）仍然没有任何基准在测** | 护城河的验收只能继续走 444 测试 + live 双场景 | ✅ 本轮**确证**（全表扫描后的负面结论） |
| 11 | 多个基准各自换默认 judge（PawBench=opus 类、PinchBench v2=Haiku） | 跨基准/跨版本分数不可比，报告必须写死 judge 口径 | ✅ 一手确证（各自 README/官方页） |
| 12 | **HCAST / RE-Bench 题多数刻意不公开**；**Vending-Bench Arena 不公开再分发**；**Mind2Web test 集加密且禁止再分发**；**WorkArena 数据集 gated** | 这四个候选**不可作为自建评测的题源** | ✅ 一手确证 |
| 13 | **SWE-bench 需 120GB 磁盘 / MLE-bench 需 3.3TB + GPU / PaperBench 需 GPU 执行容器 / OSWorld 需 KVM + 官方侧跑 / TheAgentCompany 需 30GB + 全栈 compose + host networking** | 与「Windows 开发机、无 GPU、不编排每题容器」硬冲突 | ✅ 一手确证 |

### 5.3 本轮未做 / 未取证

- `API-Bank`、`ToolBench`：**本轮未做一手取证** → 表中标【未证实】，不建议在任何决策里引用其数字。
- `GAIA2` 的细节、`CocoaBench` 的题数与许可、`RealClawBench` 的代码仓库真实地址（论文给的是匿名 4open.science）：**均未一手确证**（`Claw-Eval` 与 `WildClawBench` 的许可已于 2026-09-19 补证，见 §3.5/§3.6）。
- PawBench 站点榜单的当前行（本轮只读 README 与 NOTICE，未重跑站点抓取）：**[`pawbench-harness-interface.md`](pawbench-harness-interface.md) §五 已修正过的分数锚点（75.0 / 70.4 / 11.5）本轮未复验**。