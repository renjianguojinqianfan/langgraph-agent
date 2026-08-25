# 增量 PRD（P1 完整能力）— 基于 LangGraph 的自主任务 Agent

> 文档性质：**增量 PRD（仅描述本次变更部分，不重写整份 PRD）**
> 产品经理：许清楚（Xu）　|　版本：v0.1（P1 增量草案）　|　日期：2025-08-24
> 关联文档：现有 PRD `docs/prd.md`、架构 `docs/architecture.md`、P0 增量 `docs/incremental-prd-p0.md` / `docs/incremental-arch-p0.md`
> 项目根目录：`E:\code\demo\langgraph-agent\`

---

## 0. 背景与对标依据

本次为 **P1 完整增量实施**：在 P0（上下文压缩、熔断+重试、插件注册、trace 落盘）与前端补全（P1-0）已交付的基础上，补齐市面对标中剩余 P1 能力，共 **6 项**，走轻量 SOP。

| 市面能力 | 本次对标项 | 说明 |
|----------|-----------|------|
| HelloAGENTS EHRB 三层安全检测 | 项1 规划期风险扫描 | 关键词扫描→（可选）LLM 语义分析→危险操作执行前强制人工确认；产出 risk_report |
| HelloAGENTS 子 Agent 编排 | 项2 子 Agent 协作 | 主 Agent 派生子任务给隔离子 Agent（独立上下文/状态），串行或可选并行，结果回传 |
| 跨会话记忆 / 自建知识库 | 项3 RAG + 跨会话记忆 | 任务产物/用户文档纳入知识库，跨会话检索复用；可离线退化 |
| DeepAgent 主辅模型分工 | 项4 辅助模型分工 | 主推理模型 + 可选辅助小模型（工具选择/摘要/风险语义分析），无配置自动降级 |
| 原 PRD P1-6 | 项5 基础鉴权 | 登录 token 签发/校验落地（占位接口实现），config 开关默认关闭 |
| HelloAGENTS 可扩展工具集（OpenAPI 自动封装） | 项6 OpenAPI 工具封装完整实现 | 将 P0 的 make_openapi_tool 占位升级为真实实现 |

> 说明：原 PRD 中 P1-1（HTTP 工具）、P1-2（人工介入）、P1-3（持久化）、P1-4（重试退避）、P1-5（产物下载）在 P0 增量中已覆盖或已有实现，**不在本次重复**；本次只补 6 项。

---

## 1. 增量目标

### 一句话定位
在**不破坏现有编排内核（StateGraph + SSE）与 P0 增量能力**的前提下，补齐"执行前风险管控、子任务隔离协作、跨会话知识复用、主辅模型分工降本、基础登录鉴权、OpenAPI 一键成工具"六项 P1 能力，使 Agent 达到可对外演示、可安全执行、可低成本扩展的主流基础 Agent 水准。

### 六项各自目标与成功标准（可量化验收）

| 项 | 目标 | 成功标准（可量化） |
|----|------|--------------------|
| **1 规划期风险扫描（EHRB）** | Planner 产出计划后、执行前，对计划逐条做风险检测：关键词扫描 +（可选）LLM 语义分析；命中高风险的操作强制人工确认（或整任务暂停）；产出 risk_report | 计划含 `rm -rf` / `DROP TABLE` / 转账 / 发邮件等危险词时，risk_report 命中 ≥1 条且 level=high、含命中词与建议；命中高风险的操作在确认前**不执行**（执行被阻断）；开关关闭或未命中时任务行为与现状一致（零回归） |
| **2 子 Agent 协作** | 主 Agent 可派生子任务给隔离子 Agent（独立上下文与状态，不污染主上下文），子 Agent 可调用工具执行并回传结果；支持串行/可选并行；至少一个内置子任务场景 | 端到端"调研 X + 写报告"任务可拆为研究子任务与写作子任务并成功回传（SC1 70% 不显著下降，允许 ±10pp）；子任务完成后主 `messages` 中无子任务内部工具消息（上下文隔离可证）；并行模式下两个子任务执行时间重叠（时间戳可证）；无子任务场景主流程零回归 |
| **3 RAG + 跨会话记忆** | 任务产物/用户文档可纳入知识库（向量化或结构化索引），跨会话检索复用；任务中可通过新工具检索；可离线 | 向知识库目录放入文档并索引后，检索工具返回相关片段（关键词命中 top-k 且排序正确）；任务 A 索引的文档在任务 B 可检索到（跨会话）；无 Embedding Key 时关键词检索可用（离线）；目录缺失/索引失败不导致任务失败（零回归） |
| **4 辅助模型分工** | 主推理模型 + 可选辅助小模型（工具选择、记忆/上下文摘要、风险语义分析），配置 `aux_llm_*`；无配置自动降级主模型/规则，行为不变 | 配置 aux_llm 后，摘要/风险语义分析/工具选择确实由 aux 模型调用（日志或调用可证）；未配置时行为与现状完全一致（零回归、无额外 LLM 调用，mock 调用计数为 0）；离线模式（mock 主 + mock 辅）可跑通含摘要/风险分析的测试 |
| **5 基础鉴权（P1-6）** | 登录接口（POST /api/auth/token 占位）落地：简单 token 签发/校验（标准库或已有依赖），受保护接口校验 Bearer token，`auth_enabled` 开关默认 false | `auth_enabled=true` 时：未带 token 访问受保护接口返回 401，正确口令登录签发 token、带有效 token 返回 200，错误口令失败；`auth_enabled=false` 时现有接口零回归（无需 token）；前端可登录/登出、token 持久化、未登录跳登录页 |
| **6 OpenAPI 工具封装完整实现** | 将 P0 的 `make_openapi_tool` 占位升级为真实实现：输入 OpenAPI spec（YAML/JSON 路径或 URL）生成一组 BaseTool（每个 operation 一个工具），可注册进工具集 | 给定含 ≥2 个 operation 的 OpenAPI YAML → 生成 ≥2 个 BaseTool，name/args_schema 与 spec 一致；对可访问 API 发起调用成功并解析结果；生成工具可被 LLM 端到端调用一次；无效 spec 加载失败给出明确错误且不中断启动；`make_openapi_tool` 不再抛 NotImplementedError |

---

## 2. 用户故事（按角色/场景）

| # | 角色 | 用户故事 | 价值 |
|---|------|----------|------|
| US-P1-1 | 业务用户/安全负责人 | 作为用户，我希望 Agent 在执行删除、转账、发邮件等危险操作前先被风险扫描拦截并要我确认，避免误操作造成损失。 | 安全、可控 |
| US-P1-2 | 开发者 | 作为开发者，我希望复杂任务（如"调研+写报告"）自动拆成隔离子 Agent 串行/并行执行、结果回传主 Agent，主上下文不被子任务垃圾消息污染，从而更快拿到高质量结果。 | 长任务稳定、提速 |
| US-P1-3 | 业务用户/开发者 | 作为用户，我希望 Agent 能检索我历史任务产物与放入知识库的文档，在新任务中直接复用背景知识，而不必每次重复提供。 | 跨会话记忆、提效 |
| US-P1-4 | 开发者/成本负责人 | 作为开发者，我希望用便宜的辅助小模型做工具选择、摘要、风险分析，主模型专注核心推理，从而降低 token 成本与延迟。 | 降本、提速 |
| US-P1-5 | 运维/公网部署者 | 作为运维，我希望部署到公网时能一键开启基础登录鉴权，未授权请求被拒，本地 demo 默认免登录。 | 可安全部署 |
| US-P1-6 | 开发者 | 作为开发者，我希望把一份 OpenAPI 规范丢进去就自动生成一组可用工具，而不必手写每个 HTTP 封装。 | 可扩展、上手快 |

---

## 3. 需求池（本次全部 P0 内子点）

> 优先级说明：本次增量内 6 项均为 **P0（必须交付）**；每项拆子点，功能点 + 可量化验收 + 改动点。

### 项1：规划期风险扫描（EHRB）

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 1.1 | 触发时机 | Planner 产出 plan 后、executor 执行前插入风险扫描节点（`planner → risk_scan → executor`）；对 plan 每条 step 检测 | 每次规划后、任何工具执行前都完成一次扫描；`risk_scan_enabled=false` 时跳过（零回归） | 新增 `backend/core/agent/risk.py`；改 `nodes.py`（新增 risk_scan 节点）、`graph.py`（新增条件边） |
| 1.2 | 关键词扫描 | 内置危险词表（初版见 Q1）：删除类（rm -rf/del/删除/格式化）、数据库破坏类（DROP/DELETE FROM/TRUNCATE）、资金类（转账/支付/汇款/transfer）、外发类（发邮件/短信/发布）、系统类（关机/重启/提权）等；大小写不敏感匹配 | 计划含任一危险词 → 该 step 的 risk_item `level=high`、`matched_keywords` 列出全部命中词、`suggestion` 给出建议；未命中 → `level=low/none` | `risk.py`：`DANGER_KEYWORDS` 词表 + `scan_keywords(plan)` |
| 1.3 | LLM 语义分析（可选） | 命中词不足但语义可疑时，用辅助模型（无则主模型，见项4）对 plan step 描述做语义风险判断（high/medium/low + 理由）；`risk_semantic_enabled=false` 或无可分析模型时跳过 | 开启时对"隐晦危险描述"（如"清理服务器上所有数据"）能判为 high/medium；关闭时仅关键词扫描、行为确定 | `risk.py`：`scan_semantic(llm, step)`；`config.py` 新增 `risk_semantic_enabled` |
| 1.4 | 风险处置 | 命中 high 的操作**强制需人工确认**（复用 human_confirm 机制或新增 risk_confirm）：确认通过才执行、拒绝则跳过该操作；`risk_policy=pause` 时整任务暂停等待处置 | high 风险操作在确认前 `tool_node` 不执行（可证：事件流出现 risk 阻断、工具结果未产出）；确认后执行、拒绝后 `status=skipped` | `nodes.py`/`task_manager.py`：风险确认状态接入（复用 `_confirmed_ids/_rejected_ids` 或新增 `_risk_confirmed_ids`）；`config.py` 新增 `risk_policy` |
| 1.5 | risk_report 产出 | 扫描结果结构化：`[{step_index, level, matched_keywords[], suggestion, action(confirm|allow|block)}]`，持久化到任务并可查询 | 任务详情/接口可读到完整 risk_report（每条含等级/命中词/建议/处置）；新增 SSE 事件 `risk_report`（全量）与 `risk_found`（命中单项） | `state.py`：`AgentState` 新增 `risk_report`；`schemas.py`：`RiskItem` 模型；`routes.py`/`sse.py` 事件类型 |
| 1.6 | 工具输出复检（预留） | EHRB 第三层：对工具输出做危险内容/凭证泄露复检（轻量关键词），命中追加到 risk_report 并提示 | 本项可后置：P1 内至少保证不阻塞主流程（复检失败仅 warning）；完整实现列为 P2 候选 | `risk.py`：`scan_tool_output()` 骨架；范围边界注明 |

**配置项（config.py / .env.example 新增）**：`risk_scan_enabled`（默认 true）、`risk_semantic_enabled`（默认 false）、`risk_policy`（confirm|pause，默认 confirm）、`risk_danger_keywords`（可选覆盖词表）。

---

### 项2：子 Agent 协作

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 2.1 | 子 Agent 运行时 | 新增 SubAgent 执行器：子任务持有**独立** `AgentState`（独立 messages/plan/steps/artifacts），复用同一工具集与 LLM；子任务内事件走独立命名空间（如 `subtask_<id>`）不污染主任务事件 | 子任务完成后主 `state["messages"]` 不含子任务内部 assistant/tool 消息（上下文隔离）；子任务可正常调用工具并产出 final_answer | 新增 `backend/core/agent/subagent.py`：`SubAgentExecutor`；改 `task_manager.py`：子任务调度入口 |
| 2.2 | 派生机制 | 提供工具 `spawn_subagent`（BaseTool 子类）：入参 `{name, instruction, tools?}`，主 Agent 通过工具调用派生；返回子任务 id/结果 | LLM 端到端能调用 `spawn_subagent` 派生一个子任务并拿到结果 | `registry.py`/`tools/`：新增 `spawn_subagent` 工具；`subagent.py`：工具实现 |
| 2.3 | 串行/可选并行 | 子任务默认串行（按计划顺序执行）；`subagent_max_concurrency>1` 时可选并行（线程池调度、各自隔离状态）；同一父任务内并发上限可配 | 串行模式两子任务结束时间戳先后不重叠；并行模式 `max_concurrency=2` 时两子任务运行区间重叠（时间戳可证）；并发不会串写状态 | `subagent.py`：`SubTaskRunner`（线程池 + 隔离状态）；`config.py` 新增 `subagent_max_concurrency` |
| 2.4 | 内置子任务场景 | 至少一个内置场景：Planner 识别"调研 X 并写报告/出文档"类任务时，自动拆为「研究子任务（检索+收集素材）」+「写作子任务（基于素材写报告）」；拆分结果写入计划并执行 | 给定"调研 RAG 最新进展并写报告"任务 → 计划中出现两个子任务（研究/写作），均成功回传，最终产出报告产物 | `subagent.py`：`DEFAULT_SPLIT_SCENARIOS`；`prompts.py`：Planner 提示词增加拆分指引 |
| 2.5 | 结果回传 | 子任务 final_answer/artifacts 回传主上下文（压缩为摘要或结构化引用，不展开全部子任务日志）；主 Agent 可在后续步骤引用 | 主上下文新增内容为"子任务摘要 + 产物引用"（token 量级 ≤ 子任务全量消息的 1/10，可由 context 压缩佐证）；主 Agent 最终回答可引用子任务结论 | `subagent.py`：结果折叠（复用 `context.summarize_messages`）；`nodes.py`：回传消息组装 |
| 2.6 | 子任务可观测 | 新增 SSE 事件 `subtask_start` / `subtask_result` / `subtask_failed`（含子任务 id、名称、状态、结果摘要）；任务详情可查看子任务列表与各自状态 | 前端收到 3 类子任务事件并能按子任务 id 聚合展示（见 §4）；子任务失败不导致主任务崩溃（主任务收到失败结果并可重试/跳过） | `schemas.py`：`SubTask` 模型；`sse.py` 事件类型；`task_manager.py`：事件发布 |

**配置项（config.py / .env.example 新增）**：`subagent_max_concurrency`（默认 2）、`subagent_timeout_sec`（默认 120）、`subagent_enabled`（默认 true）。

---

### 项3：RAG + 跨会话记忆

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 3.1 | 知识库目录与文档类型 | 索引 `kb_dir`（默认 `data/kb`）下文档，支持 `.txt/.md/.json/.csv` 等文本类；文档可来自用户放入或任务产物自动入库（`kb_auto_index_artifacts`） | 目录不存在自动创建；放入受支持文档并触发索引 → 出现在知识库清单 | 新增 `backend/services/knowledge_base.py`（或 `core/kb/`）：`KnowledgeBase`；`config.py` 新增 `kb_dir`、`kb_auto_index_artifacts` |
| 3.2 | 索引方式（可离线） | 默认**结构化/关键词索引**（分块 + 词频/子串匹配，纯标准库实现）；若配置 Embedding Key 则启用向量化检索（预留接口，不做重型向量库） | 无任何 Key 时可完成索引与检索（离线）；文档被拆分为 ≤ `kb_chunk_size` 字符的块并可逐块检索 | `knowledge_base.py`：`index_documents()`、`Chunk` 模型；`config.py` 新增 `kb_chunk_size`、`kb_embedding_enabled` |
| 3.3 | 检索工具 | 新增 BaseTool `memory_search` / `kb_query`：入参 `{query, top_k?}`，返回命中片段（含来源路径/块内容/相关度） | 检索含文档关键词的 query → 返回 top-k 片段且排序合理（关键词命中优先）；未命中返回空列表而非报错 | `tools/`：注册 `memory_search`（别名 `kb_query`）；`knowledge_base.py`：`retrieve(query, top_k)` |
| 3.4 | 跨会话复用 | 知识库索引持久化到磁盘（`<kb_dir>/.index.json` 或等价结构），重启/新任务均可检索 | 任务 A 索引的文档，重启后在任务 B 中 `memory_search` 可命中（跨会话/跨重启） | `knowledge_base.py`：索引持久化 + 启动加载；`task_manager.py`：初始化 KB 单例 |
| 3.5 | 产物自动入库 | 任务产出 artifact 写盘后，若开启 `kb_auto_index_artifacts` 则自动加入知识库索引（去重） | 报告类任务完成后，产物自动可被后续任务检索；重复产物不重复索引（按路径去重） | `task_manager.py`：`add_artifact` 后触发 `kb.add_document()` |
| 3.6 | 知识库管理 REST | 新增 `GET /api/kb`（清单）、`POST /api/kb/rebuild`（重建索引）、`DELETE /api/kb/{doc_id}`（移除） | 三个接口可用且返回统一信封；重建后检索结果更新 | `routes.py`：新增 KB 路由 |

**配置项（config.py / .env.example 新增）**：`kb_dir`（默认 `data/kb`）、`kb_enabled`（默认 true）、`kb_auto_index_artifacts`（默认 true）、`kb_chunk_size`（默认 2000）、`kb_embedding_enabled`（默认 false）、`kb_top_k`（默认 5）。

---

### 项4：辅助模型分工

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 4.1 | aux_llm 配置 | 新增 `aux_llm_provider/base_url/api_key/model` 配置（前缀 `aux_llm_*`），与主模型独立；`aux_llm_enabled` 开关（默认 false） | 配置项存在且独立生效；未配置时 `create_aux_llm_client()` 返回 None（零回归） | `config.py`：`aux_llm_*` 配置 |
| 4.2 | 工厂扩展 | `openai_compat.py` 新增 `create_aux_llm_client(settings) -> LLMClient | None`：按 aux 配置构造，无配置/未启用返回 None | 有 aux 配置返回独立 client 实例（base_url/model 与主模型可不同）；无配置返回 None | `openai_compat.py`：工厂扩展 |
| 4.3 | 辅助任务接入 | 三项辅助任务优先用 aux 模型：①风险语义分析（项1.3）②记忆/上下文摘要（`context.summarize_messages` 的 llm 参数）③工具选择（可选：executor 前用小模型过滤候选工具，减少主模型 tools 体积） | 配置 aux 后三项辅助调用使用 aux 模型（日志可证）；未配置时逐项降级：①跳过语义分析（仅关键词）②摘要用主模型 ③不做工具预选（tools 全量） | `task_manager.py`：构造 aux client 并注入 `AgentRuntime`/`risk.py`/`context.py` |
| 4.4 | 无 aux 降级 | 无 aux 配置时行为与现状完全一致：不新增任何 LLM 调用 | mock 主模型调用计数在有/无 aux 配置场景下，无 aux 时与现状相同（可量化：无 aux 时辅助任务不产生额外 complete 调用） | 默认路径不改 `nodes.py` 主流程；辅助调用统一经 `get_aux_llm(settings)` 守卫 |
| 4.5 | MockLLMClient 扩展 | `MockLLMClient` 支持辅助模型场景（离线测试）：新增 `MockAuxLLMClient`（或 Mock 增加 `role` 参数），可脚本化返回工具选择/摘要/风险结论 | `use_mock_llm=true` + 配置 aux mock 时，端到端可跑通含风险语义分析/摘要的测试；测试断言辅助调用按脚本返回 | `client.py`：`MockAuxLLMClient`；`openai_compat.py`：工厂 mock 分支 |

**配置项（config.py / .env.example 新增）**：`aux_llm_enabled`（默认 false）、`aux_llm_provider`（默认同主模型）、`aux_llm_base_url`、`aux_llm_api_key`、`aux_llm_model`、`aux_llm_use_mock`（默认 false）。

---

### 项5：基础鉴权（P1-6）

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 5.1 | 登录签发 | `POST /api/auth/token` 落地：入参口令（`AuthTokenRequest.token`），校验等于 `auth_token`（或用户名+口令）；成功签发简单 token（标准库 `hmac`/`hashlib` 签名或随机串），返回 `{token, expires_at?}`；`auth_enabled=false` 时保持现状（直接 ok） | `auth_enabled=true`：正确口令 → 200 + 有效 token；错误口令 → 失败（code≠0 或 401）；token 非明文口令（签名或随机） | `routes.py`：auth 路由实现；`schemas.py`：`AuthTokenResponse`；`config.py` 已有 `auth_token` |
| 5.2 | token 校验 | 受保护接口（tasks 系列、kb、trace 等）校验 `Authorization: Bearer <token>`；简单依赖注入（`Depends`）或中间件；已签发 token 在服务端登记（内存/文件，含过期时间，用标准库实现） | `auth_enabled=true`：无/错 token → 401；有效 token → 200；过期 token → 401 | `routes.py`/`main.py`：`verify_token` 依赖/中间件；新增 `services/auth.py`（签发/校验/过期） |
| 5.3 | SSE 鉴权 | EventSource 无法带自定义 header → SSE 用 query 参数 `?token=...` 校验（或经登录后 cookie，见 Q4 默认值） | `auth_enabled=true` 时无/错 token 订阅 SSE 被拒（非 200）；带有效 token 正常收流 | `routes.py`：`task_events` 校验扩展；`services/auth.py` |
| 5.4 | 开关与零回归 | `auth_enabled`（默认 false）控制全部鉴权逻辑；关闭时接口行为与现状完全一致 | `auth_enabled=false` 时所有接口无需 token（现状回归通过） | `config.py` 已有 `auth_enabled`（默认 false） |
| 5.5 | 前端登录/登出 | 新增登录页（`auth_enabled` 时启用）：输入口令 → 调 `/auth/token` → 存储 token 至 localStorage；请求封装自动带 `Authorization` 头；登出清 token；未登录访问受保护页跳登录页 | 开启鉴权后端时：前端未登录自动跳登录页、登录后进入任务页、刷新后仍保持登录（localStorage）、登出后回到登录页 | 新增 `frontend/src/pages/LoginPage.tsx`、`components/AuthGuard.tsx`；改 `api/client.ts`（token 注入）、`App.tsx`（路由守卫）、新增 auth store |

**配置项（config.py / .env.example）**：`auth_enabled`（默认 false）、`auth_token`（已有，默认 changeme）、`auth_token_ttl_sec`（默认 86400）。

---

### 项6：OpenAPI 工具封装完整实现

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 6.1 | spec 加载 | `load_openapi_spec(source)`：支持本地 YAML/JSON 路径或 HTTP(S) URL；YAML 解析用 `pyyaml`（新增依赖，见 Q5），JSON 用标准库 | 三种来源均可加载；路径不存在/URL 不可达/格式非法 → 返回明确错误（不抛未捕获异常） | 新增 `backend/core/tools/openapi_tool.py`：`load_openapi_spec` |
| 6.2 | 工具生成 | 对 spec 中每个 operation（method+path）生成一个 BaseTool 子类：name 取 `operationId`（缺省 `method_path` 规范化）、description 取 summary/description、args_schema 从 parameters/requestBody 映射（必填/类型/枚举保留） | 含 ≥2 个 operation 的 spec → 生成 ≥2 个 BaseTool，name 唯一、args_schema 与 spec 字段一致（抽 1 个含必填参数+枚举的 operation 校验） | `openapi_tool.py`：`build_tools_from_spec(spec) -> List[Type[BaseTool]]`；`OpenAPIToolFactory` |
| 6.3 | 调用封装 | 工具 `run` 按 method/path 构造 HTTP 请求（路径参数注入 URL、query/header/body 按 spec 映射），复用 httpx；结果解析：状态码 <400 成功，返回 JSON/文本，错误携带 status_code | 对可访问的测试 API（如本地 mock server 或公开示例）发起调用成功并解析结果；4xx/5xx 返回 `success=False` + error 含状态码 | `openapi_tool.py`：`OpenAPITool.run`；复用 `httpx`（已依赖） |
| 6.4 | 安全方案 | 支持 spec 中 `securitySchemes`（apiKey header/query、basic 占位）；API Key 从配置注入（`openapi_global_headers` / `openapi_api_key`），无 Key 时仅影响需要认证的调用（明确报错） | 含 apiKey security 的 spec：配置 Key 后调用带 Key 且成功；未配置 Key 时调用返回明确错误（不影响其他 operation） | `openapi_tool.py`：security 解析；`config.py` 新增 `openapi_api_key`、`openapi_global_headers` |
| 6.5 | 注册接入 | 启动时若配置 `openapi_spec_path`/`openapi_spec_url` 且 `openapi_enabled`，加载并注册生成工具（经 `registry.register` 或并入 build_tools）；`make_openapi_tool(spec)` 占位替换为真实入口 | `openapi_enabled=true` + 有效 spec → `list_tools()` 含生成工具；`make_openapi_tool(spec)` 不再抛 NotImplementedError；无效 spec → 明确 warning 且启动继续 | `registry.py`：`make_openapi_tool` 真实实现；`task_manager.py`：启动加载注册 |
| 6.6 | 运行时/冲突处理 | 生成工具名与既有工具冲突时保留先注册者 + warning（与插件规则一致）；operation 解析失败单个跳过不中断 | 冲突场景不覆盖既有工具且有 warning；单 operation 异常不影响其他 operation 注册 | `openapi_tool.py`：复用 `registry.register` 冲突语义 |

**配置项（config.py / .env.example 新增）**：`openapi_enabled`（默认 false）、`openapi_spec_path`（默认空）、`openapi_spec_url`（默认空）、`openapi_api_key`（默认空）、`openapi_global_headers`（默认空 JSON）。

---

## 4. UI / 可视化影响

> 前端本次以**必要联动**为主，尽量复用现有组件（StepTimeline / StepDetail / ConfirmDialog / TraceTab / TaskPanel）。以下为新/改草图要点。

| 项 | 是否需要前端 | 草图要点 |
|----|--------------|----------|
| **项1 风险扫描** | ✅ 需要（联动） | 任务流中规划后插入**风险扫描横幅**：收到 `risk_report` 事件后展示 "🛡 风险扫描" 卡片，逐条显示 `{等级徽章 high红/medium黄/low灰 + 命中词 + 建议}`；命中 high 且需确认时复用 `ConfirmDialog`（标题改为"风险操作确认"），展示命中词与建议；`risk_found` 事件在对应 step 上加红色 "⚠ 高风险" 角标。 |
| **项2 子 Agent** | ✅ 需要（联动） | 任务详情页新增 **"子任务" 区域/Tab**（TaskPanel 内）：收到 `subtask_start/result/failed` 后聚合展示子任务卡片（名称、状态徽章、结果摘要），点击可展开查看该子任务的步骤摘要；主时间线中子任务位置显示 "🧩 子任务：研究" 节点。 |
| **项3 知识库** | ✅ 需要（新面板） | 新增 **知识库管理面板**（侧栏 Tab 或设置页）：文档清单（路径/大小/索引状态）、"重建索引"按钮、删除按钮；`StepDetail` 中 `memory_search`/`kb_query` 调用展示命中片段（来源 + 内容预览）。 |
| **项4 辅助模型** | ⚠️ 可选 | 设置/诊断面板展示"主模型 / 辅助模型"当前生效配置（`aux_llm_enabled` 与否），辅助任务日志可在 StepDetail 展示（可选）。非强需求。 |
| **项5 鉴权** | ✅ 需要（新页面） | 新增**登录页**（居中卡片：产品名 + 口令输入框 + 登录按钮）；顶部栏增加"登出"按钮；`App.tsx` 加 `AuthGuard`（未登录且 `auth_enabled` → 跳登录页）；`api/client.ts` 自动带 `Authorization: Bearer <token>`。 |
| **项6 OpenAPI** | ⚠️ 可选 | 设置/诊断面板展示已加载 OpenAPI 工具列表（来源 OpenAPI）；工具调用展示复用现有 StepDetail。非强需求。 |

### 前端草图要点（ASCII）

```
┌───────────────┬──────────────────────────────────────┬──────────────┐
│ 历史任务       │  任务：调研 RAG 并写报告              │ Step 详情     │
│ • 任务A   ✓   │  [消息流] [步骤] [子任务] [📜 Trace]   │              │
│ • 任务B   ⟳   │  ── 风险扫描 ──                        │ 工具: search │
│               │  🛡 Step1 high: 命中[删除, rm -rf]     │ 建议: 需确认  │
│               │  🛡 Step3 low:  命中[发邮件]           │              │
│               │  ── 子任务 ──                          │ 状态: ✓      │
│               │  🧩 研究子任务  ● 完成  结果: 3 篇素材  │              │
│               │  🧩 写作子任务  ● 完成  结果: report.md │              │
│               │  [输入框…] [发送] [停止] [登出]        │              │
│ 知识库 Tab    │  📚 文档清单: kb/rag.md ✓ 重建/删除    │              │
└───────────────┴──────────────────────────────────────┴──────────────┘
```

---

## 5. 待确认问题（极少量，均给默认值，不阻塞开发）

| # | 问题 | 建议默认值 | 影响 |
|---|------|-----------|------|
| Q1 | 风险扫描危险词表初版内容与处置策略？ | 内置词表：删除类（rm -rf/del/删除/格式化）、数据库破坏类（DROP TABLE/DELETE FROM/TRUNCATE）、资金类（转账/汇款/支付/transfer）、外发类（发邮件/短信/发布）、系统类（关机/重启/提权）；`risk_policy=confirm`（命中 high 仅需确认，不整任务暂停） | 影响项1 行为与词表维护 |
| Q2 | 知识库索引目录与产物自动入库？ | `kb_dir=data/kb`；`kb_auto_index_artifacts=true`；无 Embedding Key 时默认关键词/结构化索引（离线可用） | 影响项3 落点 |
| Q3 | 辅助模型默认关闭？ | `aux_llm_enabled=false`，未配置时全部辅助任务降级主模型/规则、零额外 LLM 调用 | 影响项4 默认行为 |
| Q4 | 鉴权默认关闭与 SSE 鉴权方式？ | `auth_enabled=false`（本地 demo 免登录）；SSE 鉴权默认 `?token=` query 参数（EventSource 不支持自定义 header） | 影响项5 前端与 SSE |
| Q5 | OpenAPI 支持范围与新增依赖？ | 首版支持 OpenAPI 3.0/3.1 的 paths 核心字段（method/path/parameters/requestBody/security）；新增依赖 `pyyaml`（仅项6 使用）；支持 apiKey header/query，basic 仅占位 | 影响项6 实现范围与 requirements |
| Q6 | 子 Agent 并行度与内置场景？ | `subagent_max_concurrency=2`（默认并行开）；内置场景由 Planner 识别"调研+报告/文档"模式自动拆分（研究 + 写作） | 影响项2 调度与演示 |

> 以上 6 项均不阻塞开发，文档已给默认值；确认后写入 `.env.example` 即可。

---

## 6. 范围边界（In / Out）

### 6.1 范围内（In Scope，本次 P1 增量）
- **项1** 规划期风险扫描（关键词扫描 + 可选 LLM 语义分析 + 高风险人工确认 + risk_report）。
- **项2** 子 Agent 协作（隔离子任务、工具派生、串行/可选并行、内置"调研+报告"场景、结果回传）。
- **项3** RAG + 跨会话记忆（知识库目录索引、可离线关键词检索、`memory_search`/`kb_query` 工具、管理 REST）。
- **项4** 辅助模型分工（`aux_llm_*` 配置、辅助任务接入、无 aux 自动降级、MockAuxLLMClient）。
- **项5** 基础鉴权（token 签发/校验、受保护接口 + SSE 鉴权、`auth_enabled` 开关、前端登录/登出）。
- **项6** OpenAPI 工具封装完整实现（spec 加载、operation 生成 BaseTool、调用封装、注册接入）。

### 6.2 范围外（Out of Scope，本次不做）
- **多 Agent 复杂编排**（supervisor 分层调度、跨 Agent 通信协议、通用 DAG 依赖编排）——本次仅内置一个子任务场景，不做通用编排（原 P2-1 部分保留）。
- **EHRB 第三层"工具输出复检"完整实现**——仅预留骨架，完整复检列 P2 候选。
- **成本/用量统计面板**（原 P2-3）——不做；辅助模型虽为降本，但不做统计面板。
- **移动端适配**（原 P2-5）——不做。
- **完整 RAG 工程化**（向量数据库、embedding 模型接入、rerank、混合检索优化、文档解析增强）——仅最小可用（关键词/结构化索引 + 向量接口占位）。
- **复杂账号体系**（多租户、OAuth、JWT 生态、角色权限）——仅简单 token 鉴权。
- **任务模板**（原 P2-2）、**插件式工具市场**（原 P2-4 商业化形态）——不做。
- **前端大改版**——本次前端仅做必要联动（登录页、风险横幅、子任务视图、知识库面板），不重构三栏布局。

---

## 附：增量对现有模块的改动汇总

| 现有模块 | 本增量改动 |
|----------|-----------|
| `backend/config.py` + `.env.example` | 新增 `risk_*` / `subagent_*` / `kb_*` / `aux_llm_*` / `auth_*` / `openapi_*` 配置 |
| `backend/core/agent/state.py` | `AgentState` 新增 `risk_report`、`subtasks`（total=False，向后兼容） |
| `backend/core/agent/nodes.py` | 新增 `risk_scan` 节点；子任务结果回传消息组装 |
| `backend/core/agent/graph.py` | 新增条件边 `planner → risk_scan → executor` |
| `backend/core/agent/risk.py` | **新增**：危险词表、`scan_keywords`、`scan_semantic`、`scan_tool_output`（骨架） |
| `backend/core/agent/subagent.py` | **新增**：`SubAgentExecutor`、`SubTaskRunner`、内置拆分场景 |
| `backend/core/agent/context.py` | `summarize_messages` 支持注入 aux llm |
| `backend/core/llm/client.py` | 新增 `MockAuxLLMClient`（离线辅助模型测试） |
| `backend/core/llm/openai_compat.py` | 新增 `create_aux_llm_client()` 工厂 |
| `backend/core/tools/registry.py` | `make_openapi_tool` 占位替换为真实实现 |
| `backend/core/tools/openapi_tool.py` | **新增**：spec 加载、`build_tools_from_spec`、`OpenAPITool` |
| `backend/core/tools/`（新增工具） | `spawn_subagent`、`memory_search`/`kb_query` |
| `backend/services/knowledge_base.py` | **新增**：索引/检索/持久化/管理 |
| `backend/services/auth.py` | **新增**：token 签发/校验/过期 |
| `backend/services/task_manager.py` | 初始化 KB/aux llm/OpenAPI 工具；子任务调度；产物自动入库；风险确认接入 |
| `backend/api/routes.py` | auth 路由落地 + 受保护接口校验；新增 KB 路由；SSE 支持 token |
| `backend/api/schemas.py` | 新增 `RiskItem`、`SubTask`、`AuthTokenResponse` 等模型 |
| `backend/api/sse.py` 协议 | 追加事件类型：`risk_report`、`risk_found`、`subtask_start`、`subtask_result`、`subtask_failed` |
| `frontend/src/**` | 新增登录页/AuthGuard/知识库面板/子任务视图/风险横幅；`client.ts` token 注入；`types/index.ts` 字段对齐 |
| `requirements.txt` | 新增 `pyyaml`（仅项6 使用） |
