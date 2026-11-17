# Git 历史、接手成本与重复工程成本审计

> 2026-11-17；代码基线 `bd1151afe3459bd3e1b1f63ebccd6ebcbe799c41`。架构追溯全部可达历史；近期窗口从 **2026-10-18 00:00 UTC** 到本次 GitHub 采集时刻。本文是有日期的审计快照，不替代现役 ADR、架构或测试命令。

## 结论先行

这个项目不是“整个架构过度设计”，而是**内核已经大幅简化，生命周期、兼容性和验证流程还保留了多代演进的接缝**。唯一 agent 节点、计划文件单源、工具能力包和装配入口值得保留；最该减少的是“同一事实要跨几套状态、三条执行路径和多份证据才能说清楚”。

近期 PR 的慢主要表现为**日历等待和堆叠传播**，不是 CI 每次跑很久：97 个 PR 周转中位数约 1.19 小时；成功 CI 中位数 111 秒。运行时值得优先验证的热点是**全量文件扫描叠加两秒轮询**，不是假想的逐 token 渲染。

本次实际落地的是一个小而确定的预防措施：把已有本机路径检查接入提交钩子，并用测试保护触发与扫描范围；同时补齐证据、退役和返工判断规则。**截至本审计快照，没有删除历史代码、没有改产品行为、没有提交、推送或创建 PR；同日作者随后授权提交/推送并开 PR #339，当前状态以 PR 为准。**

### 阅读与证据

- [97 个 PR 的逐项台账](assets/verification/history-audit-20261117-prs.md)：每项的变化、分类、返工可见性、周转和来源。
- [完整度量证据](assets/verification/history-audit-20261117.json)：97 项 PR、363 项 CI 运行索引、采集口径和合成基准。
- 现役决策入口：[ADR 状态表](agents/domain.md)、[架构](architecture.md)、[术语](../GLOSSARY.md)。

## 1. 覆盖范围与结论强度

| 证据面 | 本次覆盖 | 不应扩大成什么 |
|---|---|---|
| Git | 全部可达 908 个提交的历史元数据；关键转向用提交、ADR、现役消费者交叉核实 | 不是逐行审计每个历史版本 |
| PR | 窗口内更新的 97 项；这 97 项均在窗口内创建并合并 | 不是只取最近一页或只看标题 |
| 讨论 | PR 普通评论 151 条；91 个 Issue、126 条 Issue 评论；分页数量核对一致 | 评论中的自述仍须用代码或试验验证 |
| 正式 review / 行内评论 | API 成功查询，均为 0；仓库采用同账号评论型评审 | 不是接口不可访问，也不是未采集；不能据此说没有评审 |
| Actions | 377 次运行，其中 CI 363 次；370 个 attempt 数据集 | run、attempt、job 不能混加 |
| 现役代码 | 启动、任务生命周期、图与工具面、持久化、模型配置、文件面、SSE 与前端状态主链 | 不是全部功能的真实用户验收 |
| 运行验证 | 隔离组件基准、Mock 反例、离线套件、钩子实际执行 | 未启动网页、未调用真实模型，不能声称复现了用户界面卡顿 |

**证据分级**：直接代码、提交决策、可重复断言属于已证实；跨层后果但未跑端到端属于推断；用户体感、仓外调用者和接手人时属于未验证或估计。最终 PR body 可被修改，rebase 可替换提交；因此当前 PR 提交列表不能恢复已经被重写的完整工作过程。

## 2. 架构为何变成今天这样

| 阶段 | 当时要解决的问题 | 结果与今天的含义 | 一手证据 |
|---|---|---|---|
| 2026-08 初版 | 做一个能力完整的自主任务平台 | 确认、风险扫描、产物账本等机制同时存在，是若干残留字段的来源 | `637e70d`，初始提交 |
| 2026-08～09 续跑与迁移 | 停下后重建进程仍能继续；LangGraph 0.2 升级到 1.2 | SQLite checkpoint、格式标记、同步持久化和恢复读取保护进入核心 | [ADR 0001](adr/0001-in-place-migration-and-resume-positioning.md)、`66bae29`、`d77df67`、`48f7ab0` |
| 2026-09-15 恢复空读修复 | 已准入的 resume 在读状态时仍可能读空 | 加有限次重读；发生在引入 sync 之后，不能简单归类为“async 遗留” | `48fc1a2`；[迁移说明](migration-langgraph-1x.md) |
| 2026-09～10 完成判定探索 | 试图由系统判定交付、验证和终态 | ADR 0005–0007 代表过一条真实路线，后来被整体转向取代 | [ADR 状态表](agents/domain.md)；[ADR 0008](adr/0008-single-loop-plan-build-modes.md) |
| 2026-10-14～17 转向 | 降低完成验证栈与多节点编排的复杂度 | 一个任务是一段多轮对话；一条 loop、plan/build 两模式；正常回答后也是“停下” | `1fa0acb`、`7e130d2`；[ADR 0008](adr/0008-single-loop-plan-build-modes.md) |
| 2026-10-19～22 最简与日常形态 | 降低工具面和启动成本，让工作区、计划与模型配置可日常使用 | 产物账本退役，工作区文件替代；计划落 Markdown；单后台与模型配置形成当前外壳 | [ADR 0009](adr/0009-minimal-agent-chapter.md)、[ADR 0010](adr/0010-launch-dir-workspace-and-model-profiles.md)；PR #176–#187 |
| 2026-11-07 模型层 | 同时支持 Provider、模型条目、两种协议及旧数据 | 模型层复杂度增加有明确用途，但留下旧前端投影和旧 REST 合同 | [PR #296](https://github.com/renjianguojinqianfan/langgraph-agent/pull/296)、`e6c8b46`；[ADR 0011](adr/0011-model-config-two-protocols.md) |
| 2026-11-08 能力包 | 关闭能力必须真关，而不是只隐藏按钮或 schema | manifest 决定实例化、schema、dispatch、注入和 MCP 连接；注册不再等于挂载 | `e3c9dc1`、`de55044`；[ADR 0012](adr/0012-plugin-packages-and-presets.md) |
| 2026-11 后续稳定 | 补平台、生命周期、脱敏和测试基础设施缺口 | 很多近期“复杂代码”是已发生故障的保护，而不是可以随手删的补丁 | PR #317、#318、#320、[PR #325](https://github.com/renjianguojinqianfan/langgraph-agent/pull/325) |

### 有意为之，不应误删

1. **同步 checkpoint 与降级守卫。** `48f7ab0` 明确选择 `sync`，同时明确保留双探；`48fc1a2` 又修了恢复状态空读。今天的检查入口见 [task_manager.py:1463](../backend/services/task_manager.py#L1463)。这支持“有意保留”，不等于本次重新证明了所有上游竞态；若要去掉等待，必须做故障注入和 stop/rebuild/resume 对照，不能只看默认 durability。
2. **旧任务兼容。** `COMPLETED → INTERRUPTED`、旧 workspace、`round=0` 分别保护旧记录的状态、目录与展示。仓库之外各机器的数据存量未知，不能以当前代码不再写这些值为理由删除读取兼容。[persistence.py:48–69](../backend/services/persistence.py#L48)
3. **Task、AgentState、trace 不合成一份。** 它们分别服务 API/持久化视图、模型与 checkpoint 状态、可观测事件；LangGraph 会复制 state，外部 stop 标记仍有独立必要性。[nodes.py:191–204](../backend/core/agent/nodes.py#L191)
4. **能力包的“真关”与旧附加开关。** `de55044` 是刻意补回 legacy 开关语义，不是无人理解的偶然分支。关闭配置的行为要由完整装配面断言保护。
5. **`registers_artifact` 仍有消费者。** 主任务产物账本已退役，但子任务返回文件仍用这个标志；名字旧，不代表代码死。[base.py:36–44](../backend/core/tools/base.py#L36)
6. **单后台锁、原子替换、启动孤儿收尾、SSE 有界缓冲。** 它们各自保护进程、落盘和资源生命周期；不能用“项目只有一个用户”跳过进程重启、多个页签与多个 worker 的现实。

## 3. 历史包袱：哪些可以安全地减

这里的“可移除”是**有边界和验收条件的候选**，不是本次已经执行删除。

| 候选 | 判定 | 简化与安全门槛 |
|---|---|---|
| `requires_confirm` | 优先候选：仍被 repr 和测试读到，但不再参与运行期裁决 | 删除惰性字段与误导性说明；确认模型 schema 不变，把相关旧断言逐条映射到现役工具档位/策略断言。不能整批无说明删测试。[base.py:35,54–66](../backend/core/tools/base.py#L35) |
| MCP `danger_signal` / `classify_annotations` | 同簇候选：确认闸门的输入链已经不存在；当前只做惰性分类/调试显示 | 与上项一片退役，保留 MCP 连接、调用、错误和权限面测试。`1fa0acb` 当时为 #147 有意保留，但 [G-23](capability-registry-rescope.md) 已于 10-19 关闭 #147，原保留理由已经消失；这是有决策证据的退役候选，而不是假称“从来无用”。[mcp_tool.py:54–105](../backend/core/tools/mcp_tool.py#L54) |
| 前端旧模型 CRUD 包装与 `entriesFromProfiles` | 仓内无生产调用，只有定义/专测/注释 | 优先删前端不再消费的投影；不顺带删 `/models/{id}/default`、`test`，也不推断仓外脚本不用旧 REST。[client.ts:243–261](../frontend/src/api/client.ts#L243)、[modelCatalog.ts:23](../frontend/src/lib/modelCatalog.ts#L23) |
| Mock 的“无工具就是旧规划”分支 | 已复现的旧假设冲突，应该修而不是保留兼容借口 | 同一规划标记下，`tools=[]` 返回裸 JSON，非空工具返回 `<qianfan-plan>`；统一按模式标记决定输出，补空工具计划与执行模式用例。只证明了 Mock 响应形状，未跑网页计划落盘。[client.py:95–120](../backend/core/llm/client.py#L95) |
| registry 自动挂载表述与无调用导出 | 文档认知债；`@register` 注册并不保证进入能力包 | 把说明改成“注册 + 入包 + 装配”；优先纠正文档，再判断 `list_tools` 等调试导出是否值得保留。不要重建一套自动插件发现。[registry.py](../backend/core/tools/registry.py)、[capability_packages.py](../backend/core/capability_packages.py) |
| `_is_subtask` | 只写不读的状态键，但属于 checkpoint channel 形状 | 是退役线索，不能仅凭 grep 删除；先读旧 checkpoint fixture 做往返和未知 channel 验证，再决定是否删除。 |
| `artifact_created` 的旧 trace 展示项 | 没有现役生产者，但旧 trace 仍可能有此事件 | 删除只节省很少代码，可能损失旧 trace 标签；保留或明确标 legacy 都比优先动它更合理。[TraceTab.tsx](../frontend/src/components/TraceTab.tsx) |
| 文件工具硬编码旧目录兜底 | 条件候选，不是已证明不可达 | `BaseTool(settings=None)` 本来允许无 settings；收紧前必须查直接构造者与嵌入式用法，再统一 workspace 入口，不能为了简化随意改输入域。[file_io.py](../backend/core/tools/file_io.py) |
| 旧 `/api/models` 服务端合同、未消费的 `stream()` | 有意兼容/预留，不是即删清单 | REST 在文档中承诺过；`stream()` 体现双协议支持边界但图尚未消费。前者需消费方/迁移决策，后者应随真实流式需求重裁，不为“以后可能用”继续扩写。 |

**最小删除顺序**：先纠正注册与挂载的说明、移除前端孤儿投影；再单独做惰性确认元数据退役；Mock 错误分支作为独立 bug 修复。旧数据兼容、checkpoint 等待和安全路径检查不在第一批。

## 4. 陌生工程师要付出的认知成本

### 4.1 规模不是主要问题，跨层隐含合同才是

按当时 tracked 文件与明确后缀统计：后端生产 Python 55 个文件、14,904 行；测试 Python 73 个文件、25,706 行；前端脚本/样式（含测试）15,115 行。`TaskManager` 一份文件为 2,106 行；这些只是导航量，不是坏代码评分。

接手人必须同时理解：任务与一轮的区别、plan 模式与计划文件的区别、正常停下为何叫 `INTERRUPTED`、Task 与 checkpoint 的不同权威面、stop 请求与 worker 退出的不同时间点、三个启动/运行入口、能力包与装配工具面的差别。单看 React/FastAPI/LangGraph 技术栈清单无法建立这些模型。

**估算而非实测**：熟悉 Python、React 与异步/线程模型的工程师，用约 1 个工作日建立主链地图较合理；安全完成一项小改动可预算 2–3 天；能独立修改 stop/resume、持久化与前端竞态，宜预算 1–2 周的读测试与故障复现。实际时间取决于领域经验与可用的真实模型环境，不承诺“9.5 小时理解全部”。

### 4.2 最短阅读路线

1. [README](../README.md) → [GLOSSARY](../GLOSSARY.md) → [ADR 0008](adr/0008-single-loop-plan-build-modes.md)：先理解任务、轮次、模式和停下。
2. [runtime.py](../backend/runtime.py) + [main.py](../backend/main.py) + [cli.py](../backend/cli.py)：看清装配、服务与 REPL 的关系。
3. [graph.py](../backend/core/agent/graph.py) → [state.py](../backend/core/agent/state.py) → [nodes.py](../backend/core/agent/nodes.py)：图很小，行为复杂度主要在节点与宿主接口。
4. [task_manager.py](../backend/services/task_manager.py) + [persistence.py](../backend/services/persistence.py) + [plan_file.py](../backend/services/plan_file.py)：追一次新建、一次续说、一次停下后恢复。
5. [useSSE.ts](../frontend/src/hooks/useSSE.ts) → [taskStore.ts](../frontend/src/store/taskStore.ts) → [reduceTask.ts](../frontend/src/store/reduceTask.ts)：理解快照/事件合并，再看页面组件。
6. 按改动面补模型、能力包和测试；不要先从 archive、全部历史 ADR 或所有测试逐页顺读。

### 4.3 有收益的简化，而不是换一组类名

| 认知热点 | 建议 module / interface / seam | 收益、代价与验收 |
|---|---|---|
| `run`、`_resume_run`、`_continue_run` 重复构建 runtime、invoke、清理 | module 仍为 TaskManager；先抽内部共享的执行/清理函数，入口保留各自准入与状态准备；seam 在准备好的 state 与 graph 执行之间 | 提高 locality：加一个生命周期字段不必复制三次。先逐入口记录事件序列、落盘终态、stop 时序；不把不同的准入规则硬揉成一个巨大参数表。[1275](../backend/services/task_manager.py#L1275)、[1752](../backend/services/task_manager.py#L1752)、[2017](../backend/services/task_manager.py#L2017) |
| 节点通过 `Any/getattr` 认识宿主，子任务接触私有 stop 状态 | 明确节点真正依赖的最小宿主 interface；seam 设在停止查询、发布、脱敏等实际操作处 | 先列真实成员再决定是否需要 Protocol；不先造 TaskHost/Adapter/Manager 三层空壳。验收是调用者必须知道的隐含条件变少，且真假宿主都能通过同一接口测试。[nodes.py:119–204](../backend/core/agent/nodes.py#L119) |
| 模型选择、缓存、固定到一轮、脱敏混在宿主里 | 先区分“选择模型”与“这一轮固定模型”，只在两处以上真实调用共用时下沉 | 避免一次拆出 RoundRunner、ModelGateway、CheckpointHost 加 facade 的大手术；一片只收拢一种原因的变化。 |
| task/step/subtask 状态词不同 | 在现有术语入口给读者记录层级，不统一改 wire 值 | `INTERRUPTED` 不等于异常，step `done` 与 subtask `completed` 不代表任务拥有完成终态；收益来自解释清楚，不是大规模重命名。[schemas.py](../backend/api/schemas.py) |
| 前端快照和事件的新旧优先级散落 | 保持 `reduceTask` 纯函数，把不同快照入口的合并策略集中到这里 | 先钉乱序、续说与回放例子；引入服务端序号是另一个协议变更，不混进纯重构。 |
| plugins / packages / tools 与 plan 多义 | 优先改内部变量为 saved/active、package set/mounted tools、plan text/plan mode | 不改已存盘字段和 SSE 名；让读者能从名字看出“配置意图”还是“实际装配结果”。 |

先例主要来自本仓已经有效的做法：`plan_file` 隐藏文件与版本细节、`build_runtime` 汇合真实入口、`reduceTask` 暴露纯函数测试面。未做新的外部项目对标，本文不声称这些建议是其他 agent 项目的通行结论。

## 5. 最近 30 天：到底哪里慢

### 5.1 PR 周转：日历时间，不是人时

97 项 PR 的 `mergedAt - createdAt`：**中位 1.19h，P90 56.46h，最大 89.52h**。P90 使用 nearest-rank。将 #176–#187 这个已确认的堆叠批次单列后，其余 85 项中位 1.05h、P90 14.24h；这只是分层观察，不能把排除长尾后的数当全仓指标。

| PR | 周转 | 创建到首条评论 | 有依据的解释 |
|---|---:|---:|---|
| [#323](https://github.com/renjianguojinqianfan/langgraph-agent/pull/323) | 89.52h | 84.92h | 首次评论后的约 4.60h 内经过多轮评审/打磨；有无关图片、测试因果措辞与 body 漂移问题。前面的长空档原因没有记录，不能归咎于工程师或 CI。 |
| [#176](https://github.com/renjianguojinqianfan/langgraph-agent/pull/176) | 66.60h | 47.01h | 本片评审无返工，仍随堆叠批次等待。 |
| [#177](https://github.com/renjianguojinqianfan/langgraph-agent/pull/177) | 66.19h | 46.59h | 后台进程片有局部返工，并向后续分支传播。 |
| [#178](https://github.com/renjianguojinqianfan/langgraph-agent/pull/178) | 65.93h | 46.32h | 本片通过，后续评论明确是父片传播，不是本片再返工。 |
| [#179](https://github.com/renjianguojinqianfan/langgraph-agent/pull/179) | 65.45h | 45.83h | UNC 拒绝次序与混合分隔符导致二次修复；是有实际返工的长尾。 |
| [#180](https://github.com/renjianguojinqianfan/langgraph-agent/pull/180) | 65.19h | 45.57h | 浏览器交接的真实进程退出路径没有被注入替身测试覆盖。 |
| [#188](https://github.com/renjianguojinqianfan/langgraph-agent/pull/188) | 41.84h | 无评论 | 依赖更新；没有足够记录解释等待，不补猜测。 |
| [#228](https://github.com/renjianguojinqianfan/langgraph-agent/pull/228) | 19.20h | 18.66h | 大前端切片；大部分日历时长在首评之前，不等于前端构建用了 19h。 |

#176–#187 在 **10-22 12:19:13–12:24:06 UTC** 的 4 分 53 秒内依次合并。当前 PR 列表累计 809 条提交记录，但只有 422 个唯一 SHA；直接把 809 当工作量会重复计算 387 条传播记录。rebase 后的 committer 时间也不适合拿来重建实际工作时长。

**可优化的是交付组织**：批次里显式区分自身修改与传播、在父片裁定前避免反复把同一争议推遍所有后继片、让可选打磨不自动升级为新一轮阻塞。不是简单增加 reviewer 数量，也不是以“合得快”替代合并证据。

### 5.2 CI：执行很短，安装比编译更值得看

| 指标 | 结果 |
|---|---:|
| CI run / 成功 / 失败 / 取消 | 363 / 329 / 10 / 24 |
| 有重跑的 run / 额外 attempt | 5 / 7 |
| 成功 run 墙钟中位 / P90 / P95 / 最大 | 111s / 125s / 140s / 198s |
| 成功 run 排队中位 / 最大 | 3s / 92s |
| backend / Windows smoke / frontend 成功 job 中位 | 101s / 101s / 16s |
| 最新 attempt 的 job 总执行量 | 1121.4 分钟；并行 job 相加，不是等待墙钟 |
| 含重跑的 job 总执行量 | 1138.8 分钟 |

run 墙钟按该 attempt 启动到最后一个实际执行 job 结束计算；重跑带回的旧 job 不重复计时。CI 分位数采用线性插值。不能用 `updated_at` 冒充精确结束时间。

最慢成功样本（完整链接在度量证据的 `ci_runs`）：

| run ID | 墙钟 | 排队 | 解释 |
|---|---:|---:|---|
| 41597250159 | 198s | 83s | 排队贡献明显，关联 #283 的 master push |
| 42040403760 | 191s | 4s | Windows job 187s；不强行归到某 PR |
| 40278896424 | 187s | 87s | 排队贡献明显，关联 #215 的 master push |
| 40081871165 | 186s | 92s | 排队占约一半，关联 #194 的 master push |
| 40663212147 | 166s | 70s | 也是排队长尾，关联 #250 的 master push |

测试执行约占 job 成本 48.7%，pip 安装约 21.6%；Windows 安装中位约 35s，backend 安装约 16s。当前三个 job 并行，没有一个多余的串行编译阶段可直接拿掉。取消的 24 项都是 master push，和 concurrency 的取消策略一致；它们不是 24 次产品失败。

10 个最终失败 run 的日志涉及断言、类型、文件路径与 Windows 编码；“测试断言失败”还不等于已确认线上 bug。#154、#167、#311 三项没有匹配 CI，均是窗口内 docs-only PR；不要把其中一项误说成窗口外。

**建议**：保持必需测试与 required checks。若继续优化，先测重复安装/缓存命中与 Windows 安装长尾；为了约两分钟的流水线引入测试分片、更多矩阵或放宽覆盖，目前没有足够收益证据。

### 5.3 运行时：文件扫描已测，页面卡顿尚未验

主会话复测使用 Windows 11、Python 3.14.10、16 逻辑核，仓库外临时目录所在卷；每格 5 次，表列中位数。两种目录分别断言 Git 顶层就是合成仓库、非 Git 目录确实无父仓库；每次结果文件数也断言一致。

| 文件数 | Git 仓库扫描 | 真正非 Git 的 walk 路径 |
|---|---:|---:|
| 200 | 175ms | 130ms |
| 2,000 | 1,075ms | 1,018ms |
| 20,000 | 10,619ms（8,295–10,692） | 6,245ms（6,209–6,989） |

[workspace_files.py:70–80](../backend/core/workspace_files.py#L70) 对 Git 枚举出的条目逐个 `resolve/is_file`；[API:725–740](../backend/api/routes.py#L725) 在完整扫描后才截前 2000 个响应条目；[FilePane:44–75](../frontend/src/components/dash/FilePane.tsx#L44) 每两秒发新请求，没有等待上一次结束。**组件成本与放大链条已证实；真实浏览器请求堆积和用户卡顿仍待验。**

优先方案是前端在途去重/完成后再安排下一次轮询，先消除放大；再考虑按 workspace 共享扫描、限定枚举范围或缓存。缓存要说明 `.gitignore` 和文件变化的可见延迟；任何扫描优化必须保留符号链接、junction 和越界保护。不能为了快直接删除 `resolve`，也不能把有新语义的扫描替换成旧 `rglob` 就宣称等价。

第二个热点是 [Persistence:85–134](../backend/services/persistence.py#L85)：一次保存会在锁内序列化全部任务并原子替换。合成记录的观测如下，**不是这位用户的真实历史规模或延迟**。

| 合成 tasks.json 大小 | 重复次数 | 保存中位 |
|---|---:|---:|
| 0.55 MB | 25 | 8.17ms |
| 2.76 MB | 25 | 31.94ms |
| 11.05 MB | 12 | 144.27ms |
| 81.93 MB | 12 | 1018.66ms |

正常执行并非每个事件都落盘，不能据此渲染成“每个 token 都写全库”。但启动孤儿对账按记录调用保存，存在批量任务多次全量写回的静态放大风险。[task_manager.py:574–615](../backend/services/task_manager.py#L574) 优化时先测批量保存与锁等待；SQLite/分片属于存储迁移，需保留原子性、失败回滚与旧记录读取，不是本次顺手改格式。

反证也重要：事件发布附带 trace 的合成成本约 0.04ms/事件，不能据此说任何磁盘或极端负载都快，但本轮没有证据应优先移除 trace flush。当前图调用 `complete()` 而非 `stream()`，SSE 没有 token delta 面；“逐 token 更新 store 导致卡顿”在这个版本没有前提。长模型调用期间没有增量文本反馈是另一类体验问题，接流式会改变两种 Provider 与事件合同。

## 6. Bug、返工与重复修改的共同根因

逐项分类得到：**61 项有可见修正/返工信号，18 项明确无自身返工，18 项不可观察**。这包含自评审修正、文档纠正及独立评审返工，不是 bug 率，更不是 61 次线上事故；传播提交不单独计为新故障。未找到可达历史中标题以 `Revert` 开头的提交，但存在 rebase 丢弃内容等其他撤销形式，不能宣称“从未回滚”。

| 共因 | 不只是“发生了什么” | 代表证据 | 已有保护与下一步 |
|---|---|---|---|
| 平台输入域被换实现时缩窄 | 使用自写过滤或 shell 适配时，默认了 Linux/当前 locale 的行为 | #181 glob 反斜杠；#195 静态资源白屏；#302 requirements 编码；#325 解释器与安装/测试接口变化 | Windows 冒烟与编码测试已存在。下一步是为修改的输入域列对照，不是再造一份平台清单。 |
| 测试结果与真实执行路径脱节 | 替身绕开进程退出、组装或错误分支，正确结果掩盖了没走到目标路径 | #180 浏览器 handoff；#279 纯函数与真实失败响应；#317 私有 seam 与真正生命周期 | 回归至少有一条从实际入口走到目标失败模式；现有 conftest、真实子进程用例优先复用。 |
| 改契约时靠修改断言获得绿色 | 断言变化没有明确说是退役合同、加强保证还是削弱保证 | #163 被带走的活测试；#182 artifacts 断言；#288 恒真快照 | 已有旧→新→理由纪律，不重复新建。把旧实现反例与实际比较关系列清，避免测试只保护叙述。 |
| 文档和证据被当作事后包装 | 首版说明、符号、计数与最终 head 分离，评审变成反复校对文字 | #186 场景计数；#274 body 快照；#317 架构状态说明；#323 多轮因果纠正 | 本次加强最终文件面对账及断言口径；不允许“测试绿”推导一个测试没断言的机制。 |
| 护栏只覆盖了判定或触发的一半 | 判定规则正确，却在规范化之后才执行，或相关变更根本不会触发它 | #179 UNC/混合分隔符两轮；#283 路径白洗；文档扫描的提交入口缺失 | 本次接好现有检查并钉触发范围；新护栏需同时证明失败条件和执行入口。 |
| 脱敏按字段临时补洞 | 工具输入、结果、最终回答、URL、归档各有出口，很容易只修一个 | #184 输出副本；#272/#273 子进程与 trace；#303 token 编码；#318 证据路径 | 扩展时按出口矩阵复用 canary/扫描器；本次路径钩子不是通用密钥或截图检测器。 |
| 生命周期存在多份可变视图 | 内存、checkpoint、落盘 Task、事件回放和页面快照的收尾时机不同 | #201/#207 trace 生命周期；#214 订阅竞态；#269 多轮顺序；#317 终态步骤收尾 | 保留 `assert_no_in_flight` 与启动对账；后续针对新建/续跑/续说×正常/停止/异常做序列测试，再抽共同路径。 |
| 验证环境与产品缺陷混在同一红灯里 | 编码、临时目录、子进程环境、mock 的 hook 失效，被误读成产品回归 | #290 子进程编码；#313 真实入口；#325 pathlib 测试 seam；本次备用解释器试验 | 已有 verification-notes，先做对照再归因。本次未为宿主限制添加产品补偿层。 |

最深的一条共同根因是：**真实行为、测试断言、对外陈述没有共享同一个可验证对象。** 解决方法不是再堆一段“务必仔细”，而是把 mode、输入、最终 head、生命周期阶段、触发文件等具体对象纳入现有检查；判断题仍交给评审，不伪造机械规则。

## 7. 本次落地与后续顺序

### 已落地的最小措施

- [.pre-commit-config.yaml](../.pre-commit-config.yaml)：新增 `no-machine-paths`，复用现有扫描器；扫描 tracked Markdown、docs JSON/TXT 证据、lessons 与配置自身，钩子触发面另含扫描器/测试变更。没有新依赖、没有改 CI，没有安装或改写本机 Git hooks。
- [test_no_machine_paths.py](../backend/tests/test_no_machine_paths.py)：真实读取钩子配置，验证当前扫描面和未来同类文件能触发，也验证无关文件不触发；先见到“缺钩子”红灯，再补配置变绿。
- [workflow.md](agents/workflow.md)：补测试声明、返工收口、守卫接线、历史机制退役与性能分账五条规则。
- [security.md §3](security.md)：同步当前的提交期和离线套件检查入口。
- [verification-notes.md](agents/verification-notes.md)：补备用解释器的 `.pth` 与子进程依赖等价性检查，避免把环境不等价产生的失败当成回归。

已有 C23/C24/C25/C27/C29/C30、`check:sse`、测试隔离和终态守卫继续复用。2026-11-03 曾明确不做的用户级 Skill 改动/额外评审分工/入口压缩没有借本次复盘重新实施；没有修改全局规则或记忆。

### 建议的实施队列，不是本轮已执行

1. **性能小片**：文件面板在途去重，使用慢响应的浏览器场景验收“任一时刻每面板至多一个请求、切任务不串结果、关闭面板不请求”；保留错误与重试展示。
2. **历史小片**：前端孤儿模型投影与惰性确认元数据分开处理，分别给旧→新测试契约；旧数据读取兼容不混入。
3. **Mock 修复片**：空工具面仍按真实模式返回，覆盖 plan/build，不用扩大兼容分支解决已经不存在的旧 planner 调用。
4. **生命周期简化片**：先补三入口事件/落盘对照，再抽内部执行与清理；不一次拆出多层 manager。
5. **存储优化片**：只有在代表性历史规模和启动孤儿批次上量出收益后，才裁定批量写或 SQLite。验收包含异常、断电/损坏、旧数据与删除合同。

## 8. 验证、纠错和交付边界

以下是 2026-11-17 的审计快照数字，不是现役测试计数，也不作为长期门槛；`AGENTS.md` 的「文档不写现役测试计数」针对现役说明文档，本审计报告保留当时数字仅用于复核，后续以实跑为准。

| 状态 | 项目 |
|---|---|
| 通过 | 新接线测试：缺 hook 时按预期失败，补 hook 后通过；相邻三个测试文件 60 passed；完整离线套件 **1190 passed**；ruff、mypy 0 错；pre-commit 配置校验及真实 `no-machine-paths --all-files` 通过。 |
| 跳过 | 全量套件既有 1 项：`test_non_git_symlink_escape_is_skipped` 在 Windows 因符号链接创建权限跳过，已用 `-rs` 单独核实；没有新 skip。 |
| 未运行 | 网页功能/卡顿实测、真实模型、性能优化后的 before/after、历史代码删除后的回归；本次没有实施这些产品改动。 |
| 产物验收 | PR/Issue 分页与数量核对、CI 重新计算、报告与逐项台账交叉校验；文档链接与暴露面按最终文件执行检查。 |

**本次实际纠错**：

- 标准虚拟环境在收集阶段无法创建仓库外临时目录。只用基础解释器加 `PYTHONPATH` 又不能正确重放 `.pth` 初始化及子进程依赖：首次全量 1172 passed / 18 failed，错误含 `pywintypes`、子进程 `pydantic_settings` 缺失。随后使用仓库外一次性 Python 3.14 环境，通过 `.pth` 引用现有依赖，未安装/升级依赖、未改权限；全量通过。这个过程说明“换了解释器”不自动等于“测试环境等价”。
- 初版基准的非 Git 目录仍在项目 Git 树下，实际走父仓库枚举；该组结果作废。最终文件扫描表来自独立目录重测，原始 samples 随度量证据保存。
- 子报告把空 review 数据说成“未采集”、把一项 docs PR 说成窗口外，均已按 API 返回和创建时间更正。前端/源码体量重新按明确文件后缀计算，不用含 lockfile 的总行数冒充生产代码规模。
- 本轮没有行为等价的历史版本端到端性能对照，不把旧 `rglob` 与新可见性/路径保护不同的实现比较写成“性能回退 74 倍”。

自评审分轴记录：Standards 无硬违规，指出 2 项非阻塞文档完整性问题；Spec 无范围蔓延或实现错误，指出 1 项非阻塞耗时记录问题。对应的安全文档与钩子耗时说明已补齐。

本地分支为 `chore/history-audit-20261117`；一次性测试环境已清理，既有依赖和用户已有未跟踪文档保持不动。截至本审计快照，交付停在本地可审阅改动；随后作者授权提交/推送并开 PR #339，当前状态以 PR 为准。后续删除、协议变更与性能实现应沿上述队列逐片验收。
