---
status: accepted
---

# 产物 = 本任务写出的文件；登记源与去重都是定居决策

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
- **去重作用域按任务**：子任务把文件交回父任务，同一物理路径在父、子名下各记一条——与 ADR 0003
  「按运行各记一本账」同构，不是重复登记。
- **无活跃态即无账本**：去重比对的是 `TaskManager._active_states[task_id]["artifacts"]`；续跑时该表由
  checkpoint 带回，不在表里的 id（子任务自己的 id 就是）没有可比对象，按原样登记。

## 本票没顺手修的

子任务跑的是同一个 `tool_node`，而它的 `runtime.task_id` 是 `subtask_id`，所以一次子任务 `write` 仍会调
两次 `add_artifact`：一次记在子任务自己的 id 下（父任务产物表看不见它，只在子任务频道留一条
`artifact_created`），一次由 `subagent.py` 回记到父任务。KB 自身按 resolved path 幂等，所以代价是一条
多余事件 + 一次多余登记，而不是重复索引。#72 裁定不扩面，另票跟。

## 重开条件

- 需要让某个非沙箱工具（MCP 写文件、插件下载）的产出可下载 / 可回滚时，在包装层置
  `registers_artifact = True` 即可，**不需要**重开本 ADR。
- 要改「产物」定义本身（例如引入 dsh 那种 `present` 显式声明式交付，并和 #46 的验证锚点合并考虑），
  属于本 ADR 的重开，需要新的拍板。
