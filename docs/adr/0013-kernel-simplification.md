---
status: accepted
---

# 内核简化：兼容断代、共享后台、双 Shell 与调用观测

> 2026-11-18 由 spec [#326](https://github.com/renjianguojinqianfan/langgraph-agent/issues/326) 的 S0（#327）落库；
> 目标与验收见 [实施计划](../specs/qianfan-runtime-simplification-plan.md)，切片票 S1–S12 挂在 #326 下。
> 本文只记四项已确认裁定及其与既有 ADR 的关系，不复制切片实施细节；当前代码行为仍以
> `AGENTS.md`「跨任务安全边界」表与代码为准（本文是**目标态**，随 S1–S12 落地，不提前改写现态说明）。

## 背景

qianfan 现在能完成日常任务，但结构、数据合同与观测面仍按「可能有外部用户、需要兼容历史」的假设搭建。
当前没有外部用户，唯一用户/维护者要的是可读、可维护、功能完整的内核。四项裁定针对的是四类重复：

- **新旧两套并存**：旧模型配置投影、旧扁平 CRUD、旧任务字段回填、旧轮次展示、旧 checkpoint 迁移仍在
  服务当前调用；读代码时必须同时理解新旧两套。
- **交互形态分叉**：终端 REPL 自建运行时并持有服务锁，与 Web 互斥；退出终端即结束，没有可共享的后台。
- **命令工具单一**：Windows 固定走 Bash，没有显式 Shell 选择；PowerShell 7 的能力与语义没有进入工具面。
- **模型调用不可追溯**：任务 trace 只有步骤 / 工具事件，没有模型请求、响应、usage 与 SDK 尝试。

## 决策

### 决策 1：兼容断代

**当前格式是唯一可接受合同。** 旧模型配置、旧任务记录、旧 checkpoint、旧接口明确拒绝且
**不改原文件**；不新增迁移程序、兼容别名或双写链。

- 旧模型面：v1 `models.json`、`/api/models` 扁平投影与 projection/CRUD、旧 `ModelProfile` 解析路径
  不再服务当前调用。
- 旧任务面：`COMPLETED` 映射、旧 workspace 回填、`round=0` 旧展示、旧工具调用输入归一化等读取回填
  不再保留；旧记录被拒绝时原文不变。
- 旧 checkpoint：只接受当前合同，不做旧图迁移；保留同步落盘、有限恢复读取、损坏处理、停止信号与
  孤儿收尾。
- 旧配置面：`data/plugins.json` 的旧包 id（如 `bash`）按现有 fail-fast 报错并给修法，不静默映射、
  不自动改写文件。
- **新建任务仍允许省略目录**，由发起入口的工作目录确定默认值——这是新建默认值，不是旧记录回填。
- 不支持格式的拒绝必须可读（说明文件、期望与修法），且不静默转换或清空。**断代不授权删除真实
  本地数据或凭据**；本机旧数据是否重置必须单独获准。
- 重开条件：出现外部用户/外部兼容要求，或某旧格式携带无法从别处恢复的数据——另起 ADR 决定迁移
  或保留读取，不沿用「先兼容着」的默认。

### 决策 2：共享后台 + 终端薄客户端

**一个服务进程独立持有运行时与单例锁，客户端只通过现有 REST/SSE 访问同一任务。**

- 后台生命周期归服务：启动 / 发现 / 就绪 / 状态 / 显式停止；客户端退出或断开**只断连**，
  运行中的任务继续。停止当前任务（`/stop`）与停止后台是两个操作，不混用。
- `qianfan`（无子命令）确保服务：发现健康实例则复用，否则 detach 启动、等待就绪、打开 Web；
  `qianfan serve` 是前台服务入口（内部/开发用）；`qianfan repl` 确保/连接服务，不强制打开浏览器；
  `start.py` 仍是开发入口，不被日常客户端接管。
- `qianfan status` 读锁文件 + `/health`，输出 pid / port / mode / instance / started_at / healthy；
  无服务时明确报「未运行」并非 0。
- `qianfan stop` 调 `POST /api/server/stop`，请求携带锁文件中的 instance；服务端用现有认证 +
  instance 校验，拒绝不匹配的请求，**不按任意 PID 杀进程**。
- 优雅停止顺序：拒绝新运行 → 停止任务并解除可取消的阻塞 I/O（含命令进程、MCP 调用）→ 等待 worker
  → 完成记录/落盘 → 关闭剩余资源 → 释放锁；超时报告未完成并非 0，不把「已收到关闭请求」写成
  「已停止」。
- 终端只调用现有任务 API + SSE，不直接访问 TaskManager / Persistence / EventBus；保留输入、
  创建/选择任务、续说、停止、模式/模型选择与错误展示，不建设复杂 TUI。
- 终端从目录 B 发起的任务归 B（即使后台从 A 启动）；Web 沿用目录选择。两个客户端同时续说由后台
  准入/互斥裁决，不新增第二套客户端状态机。恢复仍仅接受 `INTERRUPTED`，续说仍允许
  `INTERRUPTED` / `FAILED`。
- 断线/认证过期/服务不可达有明确反馈；断线后先同步权威任务快照再恢复展示，**不自动重发**创建/续说
  等可能已经成功的修改请求。认证材料不出现在 URL、命令行输出或日志。
- 重开条件：需要多个后台并行、远程访问或多用户，或需要终端专用协议——另起 ADR。

### 决策 3：Windows 默认 PowerShell 7，Git Bash 显式选择

**一个命令工具 `shell`，显式 Shell 选择 + 共同执行保障；不再有 `bash` 工具名或别名。**

- 工具名与能力包 id 均为 `shell`；参数：`command`（必填）、`shell`（可选，`pwsh` | `bash`）、
  `timeout`（可选）。工具描述注明默认与可选值。
- Windows 默认 `pwsh` 7；显式 `shell="bash"` 走 Git Bash；POSIX 只有 `bash`，默认 `bash`。
  缺少指定 Shell 给清楚错误；**不静默退到 Windows PowerShell 5.1，不在执行失败后换 Shell 重跑**。
- 结果如实返回实际子进程退出码与所用 Shell；不承诺两个 Shell 的任意脚本语义相同。
- 保留现有执行保障：工作区、环境变量白名单、输出上限、超时、停止、Windows Job/进程树、
  后台清理；Shell 分支只处理发现/启动/语义。两种 Shell 都不是沙箱。
- PowerShell 语义：启动禁用用户 profile、避免交互提示、显式 UTF-8；原生命令退出码、cmdlet 错误、
  复合语句与 `$LASTEXITCODE` 不可靠的行为用矩阵明确，不能只靠 `$ErrorActionPreference`。
  不复制 `ExecutionPolicy Bypass`，不修改执行策略或安全产品权限来让测试通过。
- 同步范围：策略规则目标名、停止绑定、子代理工具面（`shell` 保持结构性不可达）、能力包清单/预设、
  注入与文档示例；计划模式不能借新命令工具绕过只读面。
- 旧 `data/plugins.json` 的 `bash` 包 id 属不支持的旧配置，按 fail-fast 报错并给修法；
  不静默映射、不自动改写文件（决策 1）。
- 重开条件：需要第三种 Shell、Shell 沙箱或跨 Shell 兼容层——另起 ADR。

### 决策 4：模型调用并入现有任务 trace

**扩展现有 `TraceRecorder` / 任务 trace，不新建平行 call-record 存储。**

- 覆盖任务内主任务、子代理与压缩调用；模型连通性测试没有任务归属，保持独立结果。
- 逻辑调用用 `call_id`，实际 HTTP 尝试用 `attempt`。调用上下文在 `LLMClient.complete` 边界建立；
  实际请求与尝试在 Provider 的 HTTP transport/request-response hook 层捕获，覆盖 SDK 合并参数后的
  请求体与每次内部重试——只记录适配器准备的 payload 不算完成。无 HTTP 的 Mock 调用明确标记，
  不伪造网络尝试。
- 模型调用以 log-only 事件 `llm_call` 写入现有 `data/traces/<task_id>.jsonl`，与 step/tool 事件共用
  同一生命周期（`trace_end`、任务删除清理）；不另开文件句柄、不建平行文件。
- `llm_call` 至少包含：`call_id`、`attempt`、`owner_task_id`、`source`（`main` / `subagent` /
  `compaction`）、`round` / `step`（适用时）、`model`、`protocol`、`started_at` / `ended_at`、
  `status`（`ok` / `error` / `aborted`）、`request`、`response`、`error`、`truncated`、`usage`
  （未返回则为未知，不估算）。
- 响应保留实际返回的 JSON/错误，提取已有 `raw` 中的 usage；未返回的用量标未知，不估算冒充实数。
- 脱敏只作用于 trace 副本：API Key、认证头、敏感 URL 参数不落 trace；实际发给模型的参数不因脱敏
  改变。较大请求可截断并标 `truncated`；完整响应/错误保留，不静默截断持久记录。
- SSE 只发轻量 `llm_call` 摘要事件：`call_id`、`status`、一句话摘要、读取指针
  `(task_id, call_id)`；不广播全量请求/响应。
- 读取沿用现有 `/trace` 并增加增量查询参数：`type`（逗号分隔的事件类型）、`call_id`（可选）、
  `offset`（NDJSON 记录偏移）、`limit`（最大记录数）。默认不带参数时保持现有行为；非法参数 422、
  任务不存在 404、超出既有响应上限按现有 413/截断语义处理；不新增 call-record 端点。
- 记录失败不覆盖模型结果、不拖垮任务，但必须暴露缺失/记录错误；不新增网络重试、不改变当前 SDK
  重试策略。进程中断后的无响应调用标为未完成/中断，不补造响应；删除任务历史时调用事件随 trace
  一起清理，晚到写入不重建已删目录。
- 重开条件：需要 token 级 delta / 流式记录、云端追踪或独立 call-record 存储——另起 ADR；本决策
  只扩展现有 trace。

## 与既有 ADR 的关系

- **修订 [ADR 0009](0009-minimal-agent-chapter.md)**：决策 2–4 的工具名与 Shell 口径（`bash` →
  `shell`，Windows 默认 PowerShell 7）由决策 3 取代；决策 11「不做每次调用的完整请求视图」由
  决策 4 修订（trace 记录完整脱敏请求/响应，界面按需读取）。
- **修订 [ADR 0010](0010-launch-dir-workspace-and-model-profiles.md)**：决策 2 的旧任务 workspace
  回填由决策 1 退役；决策 3「关掉启动它的终端 = 所有任务停下」由决策 2 取代（退出客户端只断连）；
  决策 5「本章本来就要求 Git Bash」由决策 3 取代（Git Bash 显式可选，不再是默认前提）。
- **修订 [ADR 0012](0012-plugin-packages-and-presets.md)**：决策 3 的能力包 `bash` 由决策 3 改名为
  `shell`；包/预设的「真关」语义、装配面与优先级不变。
- **不冲突**：[ADR 0008](0008-single-loop-plan-build-modes.md) 的单 loop、计划/执行两模式、状态三值、
  危险动作钩子语义不变；[ADR 0011](0011-model-config-two-protocols.md) 的 Provider + 模型目录与
  双协议语义保留，只替换旧 profile 投影的解析路径（决策 1）。
- 审计文档（[2026-11 历史与维护成本审计](../history-and-maintainability-audit-2026-11.md)）是当时快照，
  不再保留具体测试计数；其结论与纠错事实仍可读，目标以本文与 #326 为准。

## 被否的选项

| 题 | 选定 | 被否 | 否掉的理由 |
|---|---|---|---|
| 兼容策略 | 当前格式唯一，旧格式拒绝且原文不变 | 静默转换 / 迁移链 / 兼容别名 | 两套解释源正是要消除的成本；静默转换把断代藏起来，迁移链没有外部用户来偿还 |
| 后台归属 | 独立服务进程持锁 | daemon 线程 / 客户端自建运行时 | 进程退出语义不随终端；daemon 线程与 REPL 持锁都会让「退出客户端」等于停服务 |
| 终端形态 | 现有 API + SSE 的薄客户端 | 保留自建 runtime / 完整 TUI | 共享同一任务与单写者；TUI 是另一条产品线，不在本轮 |
| Shell | PS7 默认 + Git Bash 显式 | 继续只走 Bash / 失败回退 PS5.1 | 用户日常终端是 PS7；静默降级会让退出码与语义不可信 |
| 调用记录 | 扩展现有任务 trace | 平行 call-record 存储 / 下载端点 | 生命周期、删除与读取已有唯一入口；第二套存储会再造一个「同一事实两处」问题 |
| trace 脱敏 | 只改记录副本 | 改实际请求以复用脱敏结果 | 观测不能改变模型输入；否则记录与真实调用不一致 |
| SSE 载荷 | 摘要 + 读取指针 | 全量请求/响应广播 | 体积与密钥暴露面；详情按需从 trace 读取 |

## 三笔账（明说的代价）

1. **断代会挡住旧本地数据**：旧任务、旧模型配置与旧 checkpoint 被拒绝，而不是自动升级；本机数据
   是否重置要单独决定。这是有意的取舍，不是回归。
2. **终端不再是自足进程**：`qianfan repl` 需要可达的后台与认证；断线、认证过期、服务不可达都要
   有可读反馈，且不能自动重放修改请求。
3. **双 Shell 扩大语义矩阵**：退出码、编码、cmdlet 错误、复合语句与进程树都要分别验证；缺 `pwsh`
   是明确错误，不提供降级路径。
4. **trace 体积与维护面**：`llm_call` 会增加 trace 体积，需要截断、脱敏与读回测试；记录失败必须
   可见，不能用「任务成功」掩盖观测缺失。
