# qianfan 内核简化、共享交互与调用观测实施计划

> 状态：**目标已由本次 grilling 确认；实施尚未授权**。本文只提出工程切片、接口约束和验收要求，不代表相关功能已经实现。
> 源码基线：`bd1151afe3459bd3e1b1f63ebccd6ebcbe799c41`。依据：[历史与维护成本审计](../history-and-maintainability-audit-2026-11.md)及后续用户裁定。审计、证据、预防规则与本文按作者 2026-11-17 的授权分批提交、推送并开 PR #339；本文仍不授权代码实施、合并、清空数据或修改用户级配置。
> 发布模型：作者 2026-11-18 裁定 S0–S12 采用**单集成分支 + 单 PR**（`spec/326-kernel-simplification`），覆盖原「一个切片一个分支、一个 PR」；每片仍有独立提交、验收与报告，`S5`+`S6` 仍是一个原子交付单元。

## 1. 已确认的目标与边界

| 决策 | 最终口径 | 对工程的影响 |
|---|---|---|
| 定位（Q1/Q3/Q4） | 自用工具＋内核研究，主要验收代码/仓库任务；源码清晰可改，不建设运行时可切换内核的研究平台 | 保留 LangGraph，不做通用 SDK、多租户或额外插件框架 |
| 兼容与取舍 | 所有旧任务、旧模型配置、旧接口、旧 checkpoint 兼容都可放弃；允许功能减法，但保留项以本表为准 | 可以断代，不能把当前功能误当旧兼容删除；不为旧用户保留迁移链和别名 |
| 交互入口（Q5 修订/Q13） | Web 为主，**终端交互保留**；终端成为同一后台的轻量客户端，两端查看和继续同一任务 | 撤销“删除 REPL”的早期建议；去掉 REPL 自建运行时和与 Web 互斥的形态，不建设复杂 TUI |
| 后台生命周期（Q13） | 退出客户端不关闭后台；停止任务使用明确停止操作 | 服务进程独立持有运行时和单例锁；断开连接与停止任务不混为一谈 |
| 命令工具（Q12） | Windows 默认 PowerShell 7；Git Bash 显式可选；共享执行保障，不自动换 Shell 重跑失败命令 | 终端 UI 与执行 Shell 是两个不同层次；其他平台的 Bash 路径保留 |
| 能力（Q6） | 文件读写/搜索、命令执行、Skills、子代理、网络搜索、MCP 全部保留 | 减少重复装配和旧开关，不缩小实际能力 |
| 会话与模型（Q7/Q8/Q9） | 显式计划/执行、可编辑计划、跨重启续跑、多模型与 OpenAI 兼容/Anthropic 双协议保留 | 保留同步 checkpoint、当前格式恢复、模型参数继承和轮内模型固定 |
| 研究观测（Q10/Q11） | 默认把任务内主任务、子代理、压缩的模型调用写入现有任务 trace（重启/任务结束后仍可读）；保存脱敏请求、响应或错误；不新建平行 call-record 存储 | 扩展现有 trace 与查看能力，不是新建第二套记录，也不是把已有任务事件改个名字 |

本计划承接用户先前选择的“整体优化方案”，包含已审计的运行时性能项；grilling 细化产品取舍，没有撤销该范围。因此 S9–S11 是已选范围内的后置优化，不是新增产品功能，实施仍须证明收益。

本轮不新增 token 级流式输出、自动完成裁决、通用研究平台、云端追踪服务或 SQLite 重写。调研/文件自动化仍能使用，只是第一验收场景聚焦代码任务。

## 2. 当前事实：计划不能建立在这些误判上

| 事实 | 证据 | 约束 |
|---|---|---|
| 日常启动目前阻塞在 `uvicorn.run`，后台 daemon 线程只是等浏览器就绪 | [cli.py:92–112](../../backend/cli.py#L92)、[cli.py:317](../../backend/cli.py#L317) | 不能把 daemon 线程当作退出终端后仍存活的服务 |
| REPL 直接 `build_runtime`，自己持有锁，与 Web 互斥 | [repl.py:324–335](../../backend/repl.py#L324)、[repl.py:385–436](../../backend/repl.py#L385) | “复用内核代码”不等于“共享同一个后台/任务” |
| 旧 `ModelProfile` 仍服务当前模型调用，启动检查还使用旧投影 | [task_manager.py:850–872](../../backend/services/task_manager.py#L850)、[runtime.py:77–92](../../backend/runtime.py#L77) | 先接好当前运行接口，再删除兼容类型和旧 CRUD |
| 模型客户端按模型条目缓存在整个 TaskManager 中，可被多个任务/轮次复用 | [task_manager.py:850–880](../../backend/services/task_manager.py#L850) | 不能在共享客户端上挂可变的 `current_task_id` 来归属调用记录 |
| 压缩有独立模型调用，不只有 plan/build 两处 | [context.py:385](../../backend/core/agent/context.py#L385)、[nodes.py:546–561](../../backend/core/agent/nodes.py#L546) | 主任务、子代理和压缩均须覆盖 |
| Provider 的最后请求组装与原始响应在适配器内 | [openai_compat.py:88–120](../../backend/core/llm/openai_compat.py#L88)、[anthropic_native.py:89–125](../../backend/core/llm/anthropic_native.py#L89) | 从最终 Provider 请求取证，不能从 Task/对话列表事后重建 |
| `LLMResponse.raw` 已承接 Provider 原始响应，usage 可能已在其中 | [client.py:26–37](../../backend/core/llm/client.py#L26)、[anthropic_native.py:379](../../backend/core/llm/anthropic_native.py#L379) | 不宣称 usage 已丢失；提取实际返回值，未返回就标未知 |
| OpenAI 构造未指定 `max_retries`；本机安装 SDK 默认值为 2 | [openai_compat.py:56](../../backend/core/llm/openai_compat.py#L56)、[依赖约束](../../requirements.txt) | 没有应用层 retry 循环不能证明没有 HTTP 重试；实施时复核钉版 SDK，区分逻辑调用与尝试 |
| 当前命令工具明确执行 `bash -c`，同时承担 Job、停止、超时和有界输出 | [bash.py:531–659](../../backend/core/tools/bash.py#L531) | 切换 Shell 不能顺带删掉通用执行保障 |
| 文件 API 完整扫描后才截前 2000 项，面板按固定两秒轮询 | [routes.py:714–740](../../backend/api/routes.py#L714)、[FilePane.tsx:44–75](../../frontend/src/components/dash/FilePane.tsx#L44) | 先减少请求放大，再优化枚举；不能把响应限额当扫描限额 |

### Shell 已有证据及未验部分

本次只读探针在 PowerShell 7.6.9 与 Git Bash 5.3.15 上进行，普通 Node 子进程、关闭用户 profile：空启动各 7 次，中位约 334ms / 51ms；原生命令退出 7 时普通 `pwsh -Command` 返回 1，Bash 返回 7；Git Bash 会转换 `/tmp/...` 原生参数；本机 PowerShell 的 Console 输出为 GB2312，显式 UTF-8 后中文正确。仅设 `ErrorActionPreference=Stop` 不保证原生命令失败会终止脚本。

这些是启动与语义探针，不是现有 Python 工具执行链、Windows Job Object、停止/后台清理的验收。Store 安装的 PowerShell 在普通子进程中可运行，不代表所有受限启动环境都已验证，也不据此要求重装或放宽权限。

先例不统一：Codex 本次检出的 Windows 默认选择 PowerShell（`ed8eac408`，`codex-rs/shell-command/src/shell_detect.rs`）；Pi 本次检出默认 Git Bash 并提供 PowerShell 工具（`90ed3e3b1`，`packages/coding-agent/docs/windows.md`）。本项目选择来自用户裁定，不声称是生态多数。

官方语义依据：[PowerShell 原生命令参数](https://learn.microsoft.com/en-us/powershell/scripting/whats-new/what-s-new-in-powershell-73)、[启动与退出码](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pwsh?view=powershell-7.5)、[MSYS2 路径转换](https://www.msys2.org/docs/filesystem-paths/)。

## 3. 目标职责与接口

以下名称描述拟议职责，不要求新增同名类，更不要求每行都造一层 Manager。

| module | 对外 interface / seam | 只负责什么 |
|---|---|---|
| 后台生命周期 | 启动/发现/就绪/状态/显式停止 | 唯一服务进程、锁、端口、配置与资源关闭；客户端不持有运行时 |
| 任务运行 | 当前 Task + 准入 + 单轮执行/收尾 | 新建、恢复、续说、停止和终态；三种入口不再各维护一套执行核心 |
| 模型解析与适配 | 明确的 `ResolvedModel` + `LLMClient.complete` | 当前模型解析、参数继承、协议适配；不依赖旧 profile 投影，凭据不进入可公开序列化配置 |
| 命令执行 | 一个命令工具 `shell`（参数 `command` / `shell` / `timeout`），显式 Shell 选择 + 共同执行结果 | 工作区、环境过滤、输出限制、超时、停止和进程树；Shell 分支只处理发现/启动/语义 |
| 模型调用记录 | 扩展现有 `TraceRecorder` / 任务 trace；不可变调用上下文 + 实际请求尝试 + 脱敏副本 | 关联、完整性、脱敏和生命周期；不新建平行存储，不影响模型决策或重试 |
| Web / 终端 | 当前任务 API + SSE + 按需详情 | 展示与用户操作；不直接访问 Persistence、EventBus 或 TaskManager 私有状态 |

Task、AgentState/checkpoint、trace 仍各有用途，不为了“唯一来源”强行合并；模型调用事件并入 trace，而不是另建一套记录。需要收敛的是各自职责和更新入口，而不是把不同用途的数据混成一个对象。

## 4. 工程约束与合理默认

### 4.1 断代与本地数据

- 当前格式成为唯一可接受合同；旧模型迁移、旧状态映射、旧轮次展示和旧工作区补值删除，不新增迁移程序或兼容别名。
- 新建任务仍允许省略目录，由**发起入口的工作目录**确定；读盘旧记录回填与新建默认值是两回事。
- 不支持的格式明确拒绝且不改原文件，不静默转换或清空。新格式损坏、原子写失败和当前版本重启恢复仍有真实测试。
- 验收使用独立新数据目录，不读改实际任务和凭据。本机旧数据是否另行重置，必须单独获准。
- checkpoint 的简单版本/完整性判据可以保留；去掉历史年代分支不等于允许错误格式被误读。

### 4.2 共享后台与客户端

- 服务进程独立于终端客户端存活；单例锁归服务所有。并发冷启动只能有一个服务进入运行时装配和写盘。
- 沿用现有 FastAPI + REST/SSE，不迁到 LangGraph Agent Server，也不引入第三方平台。`start.py` 仍只作开发入口。
- 日常 `qianfan` 确保服务并打开 Web；`qianfan repl` 确保/连接服务，不强制打开浏览器。终端只需 API，缺前端构建不应妨碍终端使用。
- 为独立后台提供明确的状态与优雅停止操作；停止后台与 `/stop` 当前任务区分。停后台须校验本实例身份、走现有本地认证约束，不按任意 PID 杀进程。命令口径：`qianfan status` 读锁文件 + `/health`，输出 pid / port / mode / instance / started_at / healthy，无服务时明确报「未运行」并非 0；`qianfan stop` 调 `POST /api/server/stop` 并携带锁文件中的 instance，instance 不匹配被拒且不杀进程；`qianfan serve` 是前台服务入口，`start.py` 仍是开发入口。
- 关闭顺序为拒绝新运行→请求停止任务并解除可取消的阻塞 I/O（含所属命令进程、MCP 调用）→等待 worker→完成记录/落盘→关闭剩余资源→释放锁；超时须报告未完成，不把“已收到关闭请求”写成“已停止”。
- 后台日志复用 [现有日志模块](../../backend/utils/logging.py)，启动期 stdout/stderr 与异常也必须有可定位的本地去处；不丢进无人读取的管道，不重复倾倒模型请求全文。就绪前失败由启动客户端报告原因和日志位置；进程意外退出由客户端连接/health 检查识别，明确提示后台失联与重新连接/启动，不自动重发任务。日志写入失败必须显式暴露，不能宣称诊断记录完整。
- 终端退出或断开只断连；运行中的任务继续。运行中 Ctrl-C / `/stop` 请求停止当前任务，不关闭共享后台。
- 终端从目录 B 发起任务时，即使后台是在 A 启动，也必须显式发送 B；Web 继续沿用其目录选择。此规则进入端到端测试。
- 两个客户端同时续说时，由后台的当前准入/互斥规则裁决，不增加第二套客户端任务状态机。恢复仍仅接受 INTERRUPTED，续说仍允许 INTERRUPTED/FAILED。
- 断线后先同步权威任务快照，再恢复展示；不自动重发创建/续说等可能已经成功的修改请求。不假定 SSE 有无限回放或可靠的旧消息补齐。
- 客户端沿用认证接口；不读取模型 Key，不在 URL、命令行输出或日志中暴露认证材料。既有接口足够时不新造一套终端专用协议。

### 4.3 PowerShell 7 / Git Bash

- Windows 默认 `pwsh` 7，Git Bash 使用显式选择；POSIX 保持 Bash。缺少指定 Shell 给清楚错误，不静默退到 Windows PowerShell 5.1，不在执行失败后换 Shell 重跑。工具名与能力包 id 均为 `shell`；参数 `command`（必填）、`shell`（可选，`pwsh` | `bash`）、`timeout`（可选）；结果如实返回实际子进程退出码与所用 Shell。
- 统一一个命令工具及执行器；模型可见描述、工具结果与观测均注明实际 Shell。更新能力包、策略规则目标名和停止绑定，不留旧工具别名。旧 `data/plugins.json` 的 `bash` 包 id 属不支持的旧配置：按现有 fail-fast 报错并给修法，不静默映射、不自动改写文件。
- 启动禁用用户 profile、避免交互提示；显式处理 UTF-8。PowerShell 的原生命令错误、cmdlet 错误、复合语句及退出码用矩阵明确，不能只靠 `$ErrorActionPreference`。
- 结果中的退出码诚实表示实际子进程退出码；不能用陈旧 `$LASTEXITCODE` 掩盖 cmdlet 失败，也不承诺两个 Shell 的任意脚本语义相同。
- 保留现有环境白名单、输出截断说明、超时、停止、Windows Job/进程树和后台清理。两种 Shell 都不是沙箱；不复制先例中的 `ExecutionPolicy Bypass`，不修改执行策略或安全产品权限来让测试通过。自己的输出流明确使用 UTF-8，第三方原生命令的编码差异不能假定已被统一。
- 文件工具仍优先承担读写/搜索；Shell 负责命令执行，不把常规文件操作全部搬回脚本。

### 4.4 默认完整的模型调用观测

- 覆盖任务内主任务、子代理和压缩调用。模型连通性测试没有任务归属，保持其独立结果，不把它塞进任意任务的调用历史。
- 逻辑调用用 `call_id`，实际 HTTP 尝试用 `attempt`。记录 owner task、子任务来源、轮/step（适用时）、用途、模型/协议、时间、结果或错误；客户端实例共享不等于记录上下文共享。
- 逻辑调用上下文在 `complete` 边界建立；真实请求与尝试在 HTTP transport/request-response hook 层捕获，覆盖 SDK 合并参数后的请求体和每次内部重试。仅记录适配器准备的 payload 不算完成此项。无 HTTP 的 Mock 调用明确标记，不伪造网络尝试。
- 响应保留实际返回的 JSON/错误，提取已有 raw 中的 usage。未返回的用量标未知，不用估算冒充实数；不承诺获取未返回的内部思考。
- 不为观测新增网络重试，也不悄悄改变当前 SDK 重试策略；已有实际尝试全部可见。记录失败不能导致再次调用模型。
- 脱敏作用于记录副本，不能修改实际发给模型的参数。API Key、认证头和敏感 URL 参数不落 trace；任务正文/代码仍按本地敏感资料处理，不承诺识别所有未知秘密。
- 模型调用以 log-only 事件 `llm_call` 写入现有任务 `data/traces/<task_id>.jsonl`，与 step/tool 事件共用同一生命周期；不新建平行 call-record 文件。`llm_call` 字段至少含 `call_id`、`attempt`、`owner_task_id`、`source`（`main` / `subagent` / `compaction`）、`round` / `step`（适用时）、`model`、`protocol`、`started_at` / `ended_at`、`status`（`ok` / `error` / `aborted`）、`request`、`response`、`error`、`truncated`、`usage`（未返回则为未知，不估算）。任务结束/重启后 trace 仍可读。
- 较大请求可截断并标 `truncated`；完整响应/错误保留。SSE 只发轻量 `llm_call` 摘要事件：`call_id`、`status`、一句话摘要、读取指针 `(task_id, call_id)`；不能把全量上下文广播给每个客户端。
- 调用列表从 trace 过滤/分页读取，详情按需读取。沿用现有 `/trace` 并增加增量查询参数：`type`（逗号分隔的事件类型）、`call_id`（可选）、`offset`（NDJSON 记录偏移）、`limit`（最大记录数）；默认不带参数时保持现有行为，非法参数 422、任务不存在 404、超出既有响应上限按现有 413/截断语义处理，不新增 call-record 端点。页面可以折叠或限制预览，但必须能读到 trace 里的完整记录（沿用 `/trace` 读取/导出）；不能为了界面流畅静默截断持久记录。
- trace 写入失败遵循现有观测系统的 best-effort 原则：不覆盖模型结果、不拖垮任务，但必须暴露缺失/记录错误，不能显示成完整成功。进程中断后的无响应调用标为未完成/中断，不补造响应。
- 删除任务历史时 trace（含模型调用事件）一起清理，沿用现有 trace 清理与失败反馈；关闭/删除与真正的 worker 生命周期联动，避免晚到写入重建已删目录。
- S7 只记录 assembled 响应；token 级 delta 行另开切片，不把 `stream()` 引入 agent loop。

## 5. 切片与依赖

准备和收口之外，按下面工作块组织。**S5 与 S6 是同一个原子交付单元，不能拆成两个独立切片/验收单元**：服务持锁与终端接入必须一起完成，避免中间状态只改后台却留下旧 REPL。整份计划按文首「发布模型」在单一集成分支上推进，最终一个 PR 合入；其余切片仍独立验收，默认串行集成；语义可独立不代表可以同时改同一份 TaskManager、配置或文档。

### S0：准备——明确新合同与基线

- 实施前同步用户新裁定到现役决策/规则：旧兼容可退役、终端共享后台、双 Shell、默认模型调用记录；四项裁定落为 [ADR 0013](../adr/0013-kernel-simplification.md)，历史审计保留当时语境并指向新决定，不伪装成当时就已实现。
- 对当前新建/恢复/续说、模型选择、工具面和事件序列补必要现状测试，在改实现前确认通过。基线测试文件与「旧断言 → 新合同 → 原因」清单见 [326-legacy-assertions.md](326-legacy-assertions.md)。
- 清单逐条写「旧断言 → 新合同 → 原因 → 迁移/退役去向」。退役规格的断言可以撤销/改写，真实功能覆盖迁到新接口，不删测试换绿色。
- 验收：既有审计改动隔离；基线可复跑；关键接口和数据副作用有清单，不触碰实际数据。

### S1：模型解析与接口收口

- 文件面：[model_store.py](../../backend/services/model_store.py)、[task_manager.py](../../backend/services/task_manager.py)、[runtime.py](../../backend/runtime.py)、[main.py](../../backend/main.py)、[routes.py](../../backend/api/routes.py)、前端模型 API/store/ProviderCard。
- 用明确运行配置替代 ModelProfile 的兼容用途；替换 resolve/get/list_public 的当前消费者和启动检查后，删旧扁平 CRUD、v1 迁移及备份重试链、无调用前端投影。
- 默认模型、连接测试、Provider/ModelEntry CRUD、目录发现、参数继承与密钥轮换仍完整；有效现役路由不因名称看起来旧而强制改名。
- 验收：两协议请求映射/参数、默认与显式模型、删模型后不偷偷换 Provider、Key 轮换/脱敏；旧格式明确拒绝且原文件不变；Web 配置和模型选择可用。

### S2：任务与 checkpoint 的当前合同

- 文件面：[schemas.py](../../backend/api/schemas.py)、[persistence.py](../../backend/services/persistence.py)、[state.py](../../backend/core/agent/state.py)、TaskManager、前端 rounds/types/Conversation。
- 删除 COMPLETED 映射、旧 workspace 回填、round=0 旧展示、无消费者 `_is_subtask` 等状态残留；新任务保存完整当前字段，轮次定义明确。
- checkpoint 只接受当前合同；不做旧图迁移。保留同步落盘、有限恢复读取、损坏处理、停止信号与孤儿收尾。
- 验收：新建/停止/重建进程/恢复/失败后续说均能走通；新建默认目录保留；旧格式拒绝与当前格式往返分别测试，不能用静默跳记录证明成功。

### S3：单轮执行与收尾集中

- 文件面：TaskManager 的 run/_resume_run/_continue_run，[nodes.py](../../backend/core/agent/nodes.py)、[subagent.py](../../backend/core/agent/subagent.py)、必要的测试替身。
- 集中 runtime/graph 构建、invoke、异常和 finally；各入口保留准入及状态准备。恢复读取需要 graph，不能假设进入共享函数前状态总已齐备。
- 根据真实依赖收紧宿主类型与停止接口，去掉测试替身驱动的无意义 getattr 兜底；不一次拆出多个 Manager/facade。
- 修正 Mock 的“空工具=旧规划”分支，按当前模式返回；无运行消费者的 stream 预留接口可以退役，但不删除 SSE。
- 验收：三入口×正常/停止/异常的事件与读盘结果；join/互斥、trace attach 和落盘次序；无悬空步骤/工具调用；空工具 plan/build；子任务继承与父停止。

### S4：统一命令执行与能力装配

- 文件面：[bash.py](../../backend/core/tools/bash.py)及必要的通用执行模块、工具注册/能力包、TaskManager 停止绑定、Settings、注入说明、测试。
- 抽取已有进程执行保障后接 PowerShell 7，Bash 行为单独对照保留；一个命令工具显式选择两种 Shell。
- 清理 requires_confirm/danger_signal 等惰性元数据，保留策略钩子和仍有消费者的 registers_artifact；能力启停收敛到包配置，去掉重复旧闸。
- 文件工具归属走统一工作区入口；独立测试/调用使用明确依赖，不保留硬编码旧目录兜底。
- 验收：中文、空格/引号/空参数、原生退出 0/1/7、cmdlet 错误、复合语句、npm.cmd/解释器、超时、停止、后台进程、输出上限、环境过滤；真实 Python 父进程+Job 路径验证，不拿普通 Node 探针替代。
- 关能力包时仍是零实例/schema/dispatch/注入/连接；计划模式不能通过新命令工具绕过只读面。Windows 冒烟清单与规则同步。

### S5：共享后台的独立生命周期

- 文件面：[cli.py](../../backend/cli.py)、[server_lock.py](../../backend/server_lock.py)、[main.py](../../backend/main.py)、启动/认证路由与相关测试；必要时增加单一后台入口模块。
- 后台独立进程启动并持锁；客户端发现/等待就绪，不各造运行时。复用已有 instance/health/原子锁，不以 daemon 线程或空 PID 回显充当存活证明。
- 提供明确的服务状态、优雅停止路径，与任务 stop 区分。沿用本地认证、Host 与回环边界；开发入口不被日常客户端意外接管。
- 验收：两个客户端并发冷启只有一个 writer；启动方退出后台仍活；端口冲突/启动失败无误接管；启动诊断、日志不可写和后台异常退出可见；显式停后台完成任务与资源收尾，超时不报成功；随后可重启并恢复当前格式任务。

### S6：终端变为薄客户端

- 文件面：[repl.py](../../backend/repl.py)、CLI、必要的本地 HTTP/SSE 客户端、终端测试；复用现有任务接口。
- 保留基本输入、创建/选择任务、续说、停止、模式/模型选择与错误展示；通过 task id 接回同一会话，不直接访问 TaskManager/Persistence/EventBus。
- 删 REPL 自建 runtime、持服务锁和退出时 shutdown 的路径。实现连接断开、认证过期与服务不可达的明确反馈，不自动重放修改请求。
- 验收：后台从 A 启动、终端从 B 创建任务归 B；终端创建/Web观察，Web创建/终端续说，两边结果一致；终端退出不中断任务，显式 stop 有效；真实双客户端测试而非只测假 Transport。

### S7：模型调用捕获与 trace 扩展

- 文件面：LLMClient 与两协议适配器、nodes/context/subagent、[trace.py](../../backend/services/trace.py)、任务清理与 trace 读取接口；允许新增职责单一的 helper，不新增平行存储。
- 明确不可变调用上下文、logical call/HTTP attempt 关系，在实际 Provider 请求边界取证；不在缓存客户端上存可变任务归属。
- 以 log-only 事件写入现有任务 trace（`data/traces/<task_id>.jsonl`）；SSE 只增加轻量关联元数据；不新建 call-record 文件。
- 默认写入 trace，任务结束/重启后仍可读；认证材料排除、trace 副本脱敏、记录错误可见；不新增模型重试，保留并观测已有 SDK 实际尝试。
- 较大请求可截断并标 `truncated`；完整响应/错误保留；不静默截断。
- 验收：mock transport 接到的实际请求与 trace 事件除脱敏字段外一致；双协议、主/子/压缩、两个任务共享同一模型客户端并发、成功/HTTP错误/超时/中断/记录失败；假 Key canary 不出现在日志/SSE/响应/trace；原请求不因脱敏改变；重启后可读、删除及晚到写入竞态、无平行记录文件。

### S8：trace 调用查看与前端状态策略

- 文件面：任务详情/Trace 区、API/types、[taskStore.ts](../../frontend/src/store/taskStore.ts)、[reduceTask.ts](../../frontend/src/store/reduceTask.ts)、[useSSE.ts](../../frontend/src/hooks/useSSE.ts)、终端的轻量调用提示。
- Web 从任务 trace 过滤/分页读取模型调用事件，按需展开请求/响应/错误；显示模型、用途、归属、尝试和记录完整性。终端保留概要及查看位置，不复制完整 Web 调试界面。
- 完整记录读取/导出沿用现有 `/trace`；不新增 call-record 下载端点。
- 集中快照、事件、续说响应的合并策略；钉轮次、计划版本和迟到响应，不把数组长度包装成可靠版本号，不用重构掩盖尚未解决的顺序问题。
- 验收：相同文本多轮、乱序/重连、子任务并发、失败记录、超大请求预览/完整读取、任务结束/重启后可读、删除后不可再读；任务数据与网页文字按文本安全展示；真浏览器操作、控制台与网络请求检查。

### S9：文件轮询去放大

- 文件面：[FilePane.tsx](../../frontend/src/components/dash/FilePane.tsx)、文件请求方法、状态纯函数/store 测试。
- 请求结束后再安排下一次轮询；跨 effect 处理在途请求、取消和代次；保留隐藏、重试与终态刷新。取消浏览器请求不宣称服务端扫描已经停止。
- 验收：慢响应下不按固定定时器堆请求；切任务不串结果；关闭面板不再发新请求；出错可重试；正常、空目录与终态仍展示正确，浏览器网络面留证。

### S10：文件枚举减成本

- 文件面：[workspace_files.py](../../backend/core/workspace_files.py)、文件列表/工具调用点及路径测试。
- 优先缩小实际枚举范围、复用解析结果；列表上限与工具全量搜索区分。保持既定排序和可见性，不在无序枚举第 2000 项就任意停下。
- 不先建设 watcher/TTL 缓存服务；缓存若不能证明更新与忽略规则语义，宁可保留简单遍历。
- 验收：相同树输出等价，Gitignore/嵌套仓库/符号链接/junction/越界/环路不退步；Git 与真正仓库外非 Git 目录分别验证分支命中。2k/20k 合成树同条件交错前后测量，报告中位/范围及结构计数；没有稳定收益就不宣称优化成功。

### S11：批量持久化收口

- 文件面：Persistence、启动孤儿对账与失败回滚测试。
- 整批准备候选变更后一次提交，不再每个孤儿都全量 dump；零变更不写。沿用 JSON，暂不引入 SQLite/双写/迁移链。
- 当前返回活对象，不能只加 save_many 就声称原子；候选与原对象分离，写失败恢复边界明确。
- 验收：零变更零写、N 个待收尾记录只写一次、写失败内存/磁盘不半更新、重启可读、删除合同保持；记录规模基准与真实用户延迟分开。

### S12：最终收口

- 逐项检查本文件目标与验收矩阵，清理本次产生的孤儿、过期注释/接口声明，更新 README、GLOSSARY、架构、现役 ADR、设置说明与手测清单。
- 保留同一权威解释和短指针；不再复制一份 shell/模型/生命周期规则，也不修改用户级 Skill 或 Qoder 设置。
- 完整后端、Windows 敏感面、前端门禁、文档检查通过；Web/终端/两协议实际验收完成，不能仅以离线绿色宣布可用。

## 6. 推荐顺序

默认顺序为 S0 → S1 → S2 → S3 → S4 → **[S5＋S6 同片交付]** → S7 → S8 → S9 → S10 → S11 → S12。

实质依赖：S1/S2 为当前合同地基；S3 是统一运行入口；S5→S6 是共享服务/客户端链；S7→S8 是记录/查看链；S9→S10 先止放大再提速；S11 依赖当前数据与收尾合同。S4、S5、S7 的只读设计可并行，实际改动仍按共享文件冲突串行集成。

每片应留下可运行、可评审的状态；接口生产者与当前消费者同片调整，不长期保留两套实现。较大片可分准备测试与实现，但不交付半接通的功能。

## 7. 最终验收矩阵

| 场景 | 必须看到的结果 |
|---|---|
| Web/终端共享 | 同一 task id、模式、模型、计划、结果；客户端退出不等于停止任务 |
| 不同项目目录 | 后台目录与客户端目录不一致时，工具仍只从任务指定工作区起步 |
| 停止与恢复 | 新建/恢复/续说各路径在正常、异常、停止、重启之后状态一致；失败后可续说 |
| 模型与权限面 | 两协议、参数继承、显式模型失效、Key轮换；关包/计划只读/子代理范围不退步 |
| 双 Shell | 真实 Python 启动链的编码、参数、错误、超时、进程树、后台停止；无自动跨 Shell 重跑 |
| 模型观测 | 主/子/压缩与已有 SDK 尝试均可追溯，记录与实际请求对应；不泄露 canary，失败不伪装完整 |
| 文件与存储 | 轮询不堆积、路径/忽略语义不退步、批量写可回滚、任务删除联动清理 |
| 自用代码任务 | 在隔离仓库完成读代码→修改→运行验证→看文件/调用→停止/继续；网页与终端均实际使用 |

通用门禁沿用 [AGENTS.md](../../AGENTS.md) 与 [workflow](../agents/workflow.md)：ruff/mypy、后端离线套件、受影响 Windows 冒烟、前端 typecheck/test/build。SSE、设计 token、文档、工具/密钥/启动安全面按触发范围追加既有检查。涉及新 Windows-only 测试时，CI 与本地冒烟清单双址同步；不以跳过、放宽断言或跳钩子换通过。

真实模型验收覆盖两个协议；缺少某协议可用配置时写“未运行”与影响面，不用 Mock 代替。测试结果分通过/跳过/未运行/产物验收；性能数字来自同条件实跑，不写固定测试数量作为长期门槛。

## 8. 授权与退出条件

- 本轮只交付计划；产品目标已确认，不等于这些工程细节或代码实施已获授权。
- 开工前依据届时 HEAD 复核基线和既有未提交改动；不覆盖用户文件、不自动 stash/提交、不给整个工程打包一次大改。
- 格式断代不授权删除真实数据；退出客户端不授权停共享后台；研究观测不授权上传任何任务内容。
- 若发现必须改变已确认产品行为、引入新依赖/平台或改变权限设置，先报告并重新裁定；纯技术细节按本文职责与测试证据解决。
- 合格结果是“当前功能完整、单一路径易读、故障和记录可信”，不是删掉最多代码，也不是测试数量最多。

## 9. 发布记录（2026-11-17）

> 本计划已按 `/to-spec` + `/to-tickets` 发布为父 spec 与 12 张切片票；票面发布本身不含实施或代码提交授权。
> 发布边界：作者要求将审计、证据、预防规则、钩子/测试与本文分批提交到同一分支并开 PR #339；#326 的「既有未提交审计改动单独保留」在本 PR 范围内由作者显式覆盖。S0–S12 实施仍未授权。

- 父 spec：[#326](https://github.com/renjianguojinqianfan/langgraph-agent/issues/326)（标签 `spec`，按仓库规则不打 `ready-for-agent`）。
- 切片（原生阻塞边，串行；S5 与 S6 合并为同一原子交付票）：

| 切片 | 票 | 阻塞于 |
|---|---|---|
| S0 准备 | [#327](https://github.com/renjianguojinqianfan/langgraph-agent/issues/327) | 无（可立即开始） |
| S1 模型解析与接口收口 | [#328](https://github.com/renjianguojinqianfan/langgraph-agent/issues/328) | #327 |
| S2 任务与 checkpoint 当前合同 | [#329](https://github.com/renjianguojinqianfan/langgraph-agent/issues/329) | #328 |
| S3 单轮执行与收尾集中 | [#330](https://github.com/renjianguojinqianfan/langgraph-agent/issues/330) | #329 |
| S4 统一命令执行与能力装配 | [#331](https://github.com/renjianguojinqianfan/langgraph-agent/issues/331) | #330 |
| S5+S6 共享后台与终端薄客户端 | [#332](https://github.com/renjianguojinqianfan/langgraph-agent/issues/332) | #331 |
| S7 模型调用捕获与 trace 扩展 | [#333](https://github.com/renjianguojinqianfan/langgraph-agent/issues/333) | #332 |
| S8 trace 调用查看与前端状态策略 | [#334](https://github.com/renjianguojinqianfan/langgraph-agent/issues/334) | #333 |
| S9 文件轮询去放大 | [#335](https://github.com/renjianguojinqianfan/langgraph-agent/issues/335) | #334 |
| S10 文件枚举减成本 | [#336](https://github.com/renjianguojinqianfan/langgraph-agent/issues/336) | #335 |
| S11 批量持久化收口 | [#337](https://github.com/renjianguojinqianfan/langgraph-agent/issues/337) | #336 |
| S12 最终收口 | [#338](https://github.com/renjianguojinqianfan/langgraph-agent/issues/338) | #337 |

- 12 张子票均挂在 #326 下，标签 `ready-for-agent`；父票关闭条件见 S12。
