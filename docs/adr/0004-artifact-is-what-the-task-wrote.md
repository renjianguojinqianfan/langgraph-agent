---
status: retired
---

# 产物 = 本任务写出的文件；登记源与去重都是定居决策

> **已退役（2026-10-20，[C5 #160](https://github.com/renjianguojinqianfan/langgraph-agent/issues/160)）**：产物账本、`artifact_created` 与回滚快照已删除；界面改为「工作区文件」列表（ADR 0010 决策 1 / 5）。本文保留为历史决策。
>
> 2026-09-27 [#72](https://github.com/renjianguojinqianfan/langgraph-agent/issues/72) 拍板落地。
> 关联 [ADR 0003](0003-subagent-circuit-breaker-per-runtime.md)（「按运行各记一本账」是同一种定居）。

## 决策

1. **只有写类工具是产物源**：`BaseTool.registers_artifact` 默认 `False`，仅 `write` / `edit` 置 `True`。
   登记要两半齐——类声明 `True`，**且**结果 `data["path"]` 指向一个真实文件。
2. **去重键 = 任务内 resolved path**，收口在 `TaskManager.add_artifact`（两个提取点共用一处）：
   命中就回既有记录，不再铸 id、不再发第二条 `artifact_created`、不再多调一次 `add_document`。
3. **动态包装面默认不登记**：MCP / OpenAPI / 插件继承 `False`。要交付文件的包装层自己在类上置 `True`。

## 为什么值得 ADR 级记录

「产物」这个定义决定三件事的输入：完成验证 S1 校验谁、KB 自动索引吃谁、前端产物列表与回滚入口列谁。
#17 之前它是「工具结果里出现 `path`」的副产品，于是 `read` 回读自己刚写的文件会再登记一条、
`ls` 列出的目录也曾被当成产物（#17 那条 `is_file()` 守卫就是为此而加的）。同一段代码被三种定义前后改过，
说明它该由拍板来定，而不是由实现惯性来定。

四家开源 agent 的实码一致把「读过的文件」排除在产物面之外（codex 以 `HashMap<PathBuf, _>` 按路径为键、
opencode 对文件系统打快照取 diff、pi 只在写类工具上渲染 diff、dsh 拆成 `present` 显式声明 + git 工作树 diff），
逐条文件级引用记在 #72 的调研评论，不在本文复述。

## 影响面

- **读侧不再登记**：`read` / `ls` / `glob` / `grep` 的 `path` 不进产物列表、不发事件、不进 KB。
  子任务只读时向父任务交回的 `artifacts` 为空是**期望行为**，不等于「子任务没干活」。
- **同文件重写只一条**，且该记录的 `size` / `created_at` 停在**首次**登记值。S1 完成验证会重新 stat 磁盘，
  不受影响；前端产物行显示的是首次登记的元数据。
- **去重作用域按任务**：dedupe 键是「任务内 resolved path」——两个任务碰同一个文件，各记各的
  （`test_registration_is_per_task` 钉住）。**子任务写入只记父任务一条**（[#76](https://github.com/renjianguojinqianfan/langgraph-agent/issues/76) 修订本节）：
  登记点收敛到 `subagent._exec_one` 的 hand-back 单点回记父任务，`tool_node` 不再以 subtask id
  登记——子任务没有 `_active_states` 条目，那条登记本来落不了任何账本，只发一条幻影
  `artifact_created` 并多一次 KB ingest。原「同一物理路径在父、子名下各记一条」的表述随 #76 作废。
- **并发登记原子**：「查已登记 → 铸记录 → 追加入账」在 manager 锁（`TaskManager._lock`，与 resume
  claim 同一把）内一步完成——子任务跑在线程池上（`subagent_max_concurrency`），两个子任务同时把同一路径
  交回同一父任务时，无锁的 check-then-act 会双双未命中、双双登记（PR #77 评审 F1）。事件发布与 KB 入库
  留在锁外，订阅方与文件索引回调不会在持锁状态下运行。
- **无活跃态即无账本**：去重比对的是 `TaskManager._active_states[task_id]["artifacts"]`；续跑时该表由
  checkpoint 带回，不在表里的 id 没有可比对象，按原样登记。#76 之后 subtask id 不再走到
  `add_artifact`（登记收敛到 hand-back 单点，迟到 hand-back 由 `has_active_state` 拒绝并留痕）；
  让迟到记录落到 `Task.artifacts` 是 [#106](https://github.com/renjianguojinqianfan/langgraph-agent/issues/106)（裁 B）的活。

## 本票没顺手修的（#76 已跟进并落地）

子任务跑的是同一个 `tool_node`，而它的 `runtime.task_id` 是 `subtask_id`，#72 落地时一次子任务 `write` 会调
两次 `add_artifact`：一次记在子任务自己的 id 下（父任务产物表看不见它，只在子任务频道留一条
`artifact_created`），一次由 `subagent.py` 回记到父任务。KB 自身按 resolved path 幂等，所以代价是一条
多余事件 + 一次多余登记，而不是重复索引。#72 裁定不扩面，另票跟——即下述 #76，已落地。

后续票 = [#76](https://github.com/renjianguojinqianfan/langgraph-agent/issues/76)：`tool_node` 在
`_is_subtask` 为真时跳过登记，由 hand-back 单点回记父任务（一次 write 只登记一次、只发一条
`artifact_created`，父频道）；父任务已终结时的迟到 hand-back 明确拒绝并留痕（日志）。「无活跃态即无账本」
的下一句改动——让迟到记录落到 `Task.artifacts`——是 [#106](https://github.com/renjianguojinqianfan/langgraph-agent/issues/106)（裁 B）的活，与本节各改各的。

## 重开条件

- 需要让某个非沙箱工具（MCP 写文件、插件下载）的产出可下载 / 可回滚时，在包装层置
  `registers_artifact = True` 即可，**不需要**重开本 ADR。
- 要改「产物」定义本身（例如引入 dsh 那种 `present` 显式声明式交付，并和 #46 的验证锚点合并考虑），
  属于本 ADR 的重开，需要新的拍板。
